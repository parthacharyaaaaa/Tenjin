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

def processUserInfo(**kwargs) -> tuple[bool, dict]:
    '''Validate and process user details\n
    Currently accepts params:
    - username (str)
    - password (str)
    - email (str)

    returns:
    tuple of boolean and dictionary. In case of failure in validation, the bool value is False, and the immediate error message is contained in the dict. Otherwise, boolean is True and the dict contains the processed user data
    '''
    global EMAIL_REGEX
    try:
        if kwargs.get("username"):
            username : str = kwargs['username'].strip()
            if not (5 < len(username) < 64):
                return False, {"error" : "username must not end or begin with whitespaces, and must be between 5 and 64 characters long"}
            if not username.isalnum():
                return False, {"error" : "username must be strictly alphanumeric"}
        
        if kwargs.get("email"):
            email : str = kwargs['email'].strip()
            if not re.match(EMAIL_REGEX, email, re.IGNORECASE):
                return False, {"error" : "invalid email address"}
        
        if kwargs.get('password'):
            if not (8 < len(kwargs.get('password')) < 64):
                return False, {"error" : "Password length must lie between 8 and 64"}
        
        return True, {"username" : username, "email" : email, "password" : kwargs.get('password')}
    except:
        return False, {"error" : "Malformatted data, please validate data types of each field"}