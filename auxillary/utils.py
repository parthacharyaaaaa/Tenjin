'''Helper functions'''
import hashlib
import re

EMAIL_REGEX = r"^(?=.{1,320}$)([a-zA-Z0-9!#$%&'*+/=?^_`{|}~.-]{1,64})@([a-zA-Z0-9.-]{1,255}\.[a-zA-Z]{2,16})$"     # RFC approved babyyyyy

def hash_password(password: str, salt: bytes = None) -> tuple[bytes, bytes]:
    '''
    Produce a password salt and hash from a given string
    
    returns: tuple[password-hash, salt]'''
    if salt is None:
        salt = os.urandom(16)
    
    passwordHash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
    return passwordHash, salt

def verify_password(password: str, password_hash : bytes, salt: bytes) -> bool:
    '''
    Match a given password and salt with a hashed password
    '''
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000) == password_hash

def processUserInfo(username : str, email : str, password : str) -> tuple[bool, dict]:
    global EMAIL_REGEX
    try:
        username = username.strip()
        email = email.strip()

        if not (5 < len(username) < 64):
            return False, {"username_error" : "username must not end or begin with whitespaces, and must be between 5 and 64 characters long"}
        if not username.isalnum():
            return False, {"username_error" : "username must be strictly alphanumeric"}
        
        if not re.match(EMAIL_REGEX, email, re.IGNORECASE):
            return False, {"email_error" : "invalid email address"}

        if not (8 < len(password) < 64):
            return False, {"password_error" : "Password length must lie between 8 and 64"}
        
        return True, {"username" : username, "email" : email, "password" : password}
    except:
        return False, {}