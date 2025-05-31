'''SMTP Worker logic'''
import os, sys
from time import sleep, time
from dotenv import load_dotenv

from ssl import create_default_context
import smtplib

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from redis import Redis
from traceback import format_exc

from typing import Literal
from uuid import uuid4

if not load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")):
    raise FileNotFoundError()

def enqueueEmail(redisinterface: Redis, email: str, subject = Literal["deletion", "recovery", "password"], **kwargs):
    '''Enqueue an email to a global Redis queue for delivery, equivalent to pushing an element to `mail_queue` from the left. Expects all email metadata to be provided as keyword arguments.
    The subject arg is used by SMTP workers to select the appropriate email template and load kwargs. See email_templates to know which templates expects which kwargs
    \n
    '''
    try:
        uid = uuid4().hex
        added = redisinterface.hset(uid, mapping={"type" : subject, "email" : email, **kwargs})

        if added == 0:
            return False
        
        redisinterface.lpush("mail_queue", uid)
        return True
    except:
        return False
        
def dispatchFromQueue(redisinterface: Redis, cooldown_key: str = 'SSL_COOLDOWN_KEY') -> dict | None:
    '''
    Try to remove the oldest entry from `mail_queue` through `rpop`. `SSL verification countdown` is decremented by 1 upon successful retrival of mail data. Retrieval will also delete the hash containing all the data from Redis. Failures, and existence of a lock will cause None to be returned. 

    This method is safe for contention in case of multiple SMTP workers, and does not require any external logic for managing concurrency.
    '''
    try:
        qLen: int = redisinterface.llen("mail_queue")
        if not qLen:
            return
        oldestEntry: str = redisinterface.lindex("mail_queue", qLen-1)

        if not redisinterface.set(f"lock:{oldestEntry}", 1, nx=True, ex=10):
            return None
        
        # Lock set, let's do this >:D
        res: list[str] = redisinterface.rpop('mail_queue', 1)
        if not res: return None
        res: str = res[0]
        metadata: dict = redisinterface.hgetall(res)

        with redisinterface.pipeline() as pipeline:    
            pipeline.multi()
            pipeline.delete(f"lock:{oldestEntry}")
            pipeline.delete(res)
            pipeline.decr(cooldown_key)
            pipeline.execute()

        return metadata
    except Exception as e:
        print(format_exc())
        print(f"Failed to dispatch: {e}")

def manageRefresh(redisinterface: Redis, cooldown_key: str = 'SSL_COOLDOWN_KEY', cooldown_upper_bound: int = 20*60) -> None:
    '''
    Notify all SMTP workers to initiate their refresh cycle by setting key `refresh_flag` to a unique UUID in case of `SSL verification countdown` reaching 0 or lower.

    A unique UUID ensures that an SMTP worker will never perform any redundant/premature refreshes, and always be up to date with the current count of refreshes. Even failure to refresh (in case of another flag being set almost instantly) will eventually be mitigated as each SMTP worker will remember its current refresh ID.
    '''
    try:
            countdown: int = int(redisinterface.get(cooldown_key))
            if not countdown or countdown > 0:
                return
            if not redisinterface.set(f"lock:{cooldown_key}", 1, nx=True, ex=20):
                return
            
            with redisinterface.pipeline() as pipeline:
                # Lock set, reset that mf
                flag_id = uuid4().hex
                pipeline.multi()
                pipeline.set(cooldown_key, cooldown_upper_bound)
                pipeline.set("refresh_flag", flag_id, ex=120)
                pipeline.delete(f"lock:{cooldown_key}")
                pipeline.execute()
    except Exception:
        print("Failure in resetting countdown")
        print(format_exc())

def sendMail(interface: smtplib.SMTP_SSL, template: str, sender_address: str, subject = Literal["deletion", "recovery", "password"], **kwargs) -> None:

    email_message = MIMEMultipart()
    email_message['From'] = sender_address
    email_message['To'] = kwargs["email"]
    email_message['Subject'] = f'{subject}'

    body = template.format(**kwargs)
    email_message.attach(MIMEText(body, 'html'))
    interface.send_message(email_message)

def refreshConnection(bAlive: bool = True, host : str = os.environ["SMTP_HOST"], port : int = int(os.environ["SMTP_PORT"]), currentConnection : smtplib.SMTP_SSL | None = None):
    if bAlive:
        currentConnection.quit()
        currentConnection.close()

    interface = smtplib.SMTP(host, port)
    interface.ehlo()
    interface.starttls(context=create_default_context())
    interface.login(user=os.environ['SMTP_SENDER_ADDRESS'],
                        password=os.environ["SMTP_PASSWORD"])
    return interface

def backoff(default_time = float(os.environ["SMTP_BACKOFF_TIME"])):
    sleep(default_time)


if __name__ == "__main__":
    ### Redis setup ###
    RedisInterface: Redis = Redis(os.environ['REDIS_HOST'], os.environ['REDIS_PORT'], decode_responses=True)    # I've chosen to assign a separate instance to each mail worker to make them independent from RedisInterface instance used universally in resource server. This allows the mail workers to work regardless of whatever happens to the Flask workers

    ### Email templates for all possible scenarios ###
    #NOTE: It is CRUCIAL for the file names to match with the *args in the Literal type hint in the signature for sendMail, otherwise a keyError would be raised when trying to load an email template
    TEMPLATES : dict = {}
    TARGET_DIR = os.path.join(os.path.dirname(__file__), "email_templates")
    for file in tuple(os.walk(TARGET_DIR))[-1][-1]:         # Awful hack alert
        with open(os.path.join(TARGET_DIR, file), "r") as template:
            TEMPLATES[file.split(".")[0]] = template.read()
    
    currentFlag: str = '-1'
    identity = os.getpid()
    epoch = time()
    isDead = False
    timeout = int(os.environ["SMTP_TIMEOUT"])

    print(f"[{identity}] Initializing mail worker")
    SMTP_INTERFACE: smtplib.SMTP_SSL = refreshConnection(bAlive=False)
    print(f"[{identity}] Initialized mail worker")

    while(True):
        try:
            if time() - epoch > timeout and not isDead:
                print(f"[{identity}] Killing worker")
                SMTP_INTERFACE.close()
                isDead = True
                print(f"[{identity}] Killed worker")
                backoff()

            email_metadata: dict = dispatchFromQueue(RedisInterface)
            if not email_metadata:
                backoff() 
                continue

            if isDead:
                # Arise
                print(f"[{identity}] Reviving worker")

                # Update local refresh flag to the global flag, since a dead worker would have not participated in the SSL refresh flow upto this point. This will prevent an unnecessary SSL refresh right after a new connection is instantiated, since wihtout this logic the flags would almost never be in sync.
                currentFlag = RedisInterface.get("refresh_flag")
                isDead = False
                SMTP_INTERFACE = refreshConnection(bAlive=False)

                print(f"[{identity}] Revived worker")

            sendMail(interface=SMTP_INTERFACE,
                     subject=email_metadata["type"], 
                     template=TEMPLATES[email_metadata['type']],
                     sender_address=os.environ['SMTP_SENDER_ADDRESS'],
                     **email_metadata)

            print(f"[{identity}] sent mail to {email_metadata['email']}")
            email_metadata = {}

            # Reset time on success
            epoch = time()

            # Manage SSL refresh (Super secure >:3)
            manageRefresh(RedisInterface)
            flagID: str = RedisInterface.get("refresh_flag")

            if not flagID or flagID == currentFlag:
                backoff()
                continue
            
            print(f"[{identity}] Refreshing connection")
            currentFlag = flagID
            SMTP_INTERFACE = refreshConnection(currentConnection=SMTP_INTERFACE)
            print(f"[{identity}] Connection refreshed")

        except smtplib.SMTPAuthenticationError as e:
            print(f"[{identity}] Authentication Failed: ", e)
            break
        except smtplib.SMTPException as e:
            print(f"[{identity}] SMTP Exception: ", e)

            # In case email metadata exists (Not an empty dict), we will need to re-enqueue it to the mail queue for a healthy worker to process
            if email_metadata:
                enqueueEmail(RedisInterface, email_metadata['type'], **email_metadata)
                print(f"[{identity}] Re-enqueued email to {email_metadata['email']}, subject: {email_metadata['type']}")
            else:
                print(f"[{identity}] No email to re-enqueue")
            isDead = True
        except Exception as e:
            print(f"[{identity}] Unexpected Error: ", e)
            print(format_exc())
            if email_metadata:
                enqueueEmail(RedisInterface, email_metadata['type'], **email_metadata)
                print(f"[{identity}] Re-enqueued email to {email_metadata['email']}, subject: {email_metadata['type']}")
            else:
                print(f"[{identity}] No email to re-enqueue")
            backoff()