
'''Blueprint for serving HTML files. No URL prefix for these endpoints is required'''
from flask import Blueprint, render_template, request

templates: Blueprint = Blueprint('templates', __name__, template_folder='templates')

###========================= ENDPOINTS =========================###

@templates.route("/")
def index() -> tuple[str, int]:
    print(request.cookies)
    return render_template('base.html', auth = True if request.cookies.get('access', request.cookies.get('Access')) else False)

@templates.route('/login')
def login() -> tuple[str, int]:
    return render_template('login.html', minHeader = True)

@templates.route('/signup')
def signup() -> tuple[str, int]:
    return render_template('signup.html', minHeader = True)