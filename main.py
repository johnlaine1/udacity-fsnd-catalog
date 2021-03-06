from flask import Flask, render_template, request, redirect, jsonify, url_for, flash
from flask import session as login_session
import random, string
from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError
import httplib2
import os
import json
from flask import make_response
import requests
import db_controller
import config


app = Flask(__name__)

base_dir = os.path.dirname(__file__)

# ADD VARIABLES TO ALL TEMPLATES
@app.context_processor
def inject_users():
    users = db_controller.get_users()
    categories = db_controller.get_categories()
    return dict(users = users, categories = categories)


##### AUTHENTICATION #####

# Disconnect based on provider
@app.route('/disconnect')
def disconnect():
    if 'provider' in login_session:
        if login_session['provider'] == 'google':
            gdisconnect()
            del login_session['gplus_id']
            del login_session['credentials']
        if login_session['provider'] == 'facebook':
            fbdisconnect()
            del login_session['facebook_id']
        del login_session['username']
        del login_session['email']
        del login_session['picture']
        del login_session['user_id']
        del login_session['provider']
        flash("You have successfully logged out.")
        return redirect(url_for('showBooksFront'))
    else:
        flash("You were not logged in")
        return redirect(url_for('showBooksFront'))


@app.route('/fbconnect', methods=['POST'])
def fbconnect():
  # Check state to prevent against cross site forgery attacks.
  if request.args.get('state') != login_session['state']:
    response = make_response(json.dumps('Invalid state parameter.'), 401)
    response.headers['Content-Type'] = 'application/json'
    return response

  access_token = request.data

  # Exchange client token for long-lived server-side token with
  # GET /oauth/access_token?grant_type=fb_exchange_token&
  #   client_id={app-id}&client_secret={app-secret}&fb_exchange_token={short-lived-token}
  secret_file = os.path.join(base_dir, 'oauth_credentials/client_secret_fb.json')
  app_id = json.loads(open(secret_file, 'r').read())['web']['app_id']
  app_secret = json.loads(open(secret_file, 'r').read())['web']['app_secret']
  url = 'https://graph.facebook.com/oauth/access_token?grant_type=fb_exchange_token&client_id={}&client_secret={}&fb_exchange_token={}'.format(app_id, app_secret, access_token)
  h = httplib2.Http()
  result = h.request(url, 'GET')[1]

  # Use token to get user info from API
  userinfo_url = 'https://graph.facebook.com/v2.2/me'
  # Strip expire tag from access token
  token = result.split('&')[0]

  # Populate the login_session object
  url = 'https://graph.facebook.com/v2.4/me?{}&fields=name,id,email'.format(token)
  h = httplib2.Http()
  result = h.request(url, 'GET')[1]
  data = json.loads(result)
  login_session['provider'] = 'facebook'
  login_session['username'] = data['name']
  login_session['email'] = data['email']
  login_session['facebook_id'] = data['id']

  # Get user picture - Facebook requires a separate call for this.
  url = 'https://graph.facebook.com/v2.2/me/picture?{}&redirect=0&height=200&width=200'.format(token)
  h = httplib2.Http()
  result = h.request(url, 'GET')[1]
  data = json.loads(result)
  login_session['picture'] = data['data']['url']

  # Check if user exists, if not create one.
  user_id = db_controller.get_user_id_from_email(login_session['email'])
  if not user_id:
    user_id = db_controller.create_user_from_session(login_session)
  login_session['user_id'] = user_id

  output = ''
  output += '<h1>Welcome, '
  output += login_session['username']
  output += '!</h1>'
  output += '<img src="'
  output += login_session['picture']
  output += ' " style = "width: 300px; height: 300px;border-radius: 150px;-webkit-border-radius: 150px;-moz-border-radius: 150px;"> '
  flash("you are now logged in as %s" % login_session['username'])
  return output


@app.route('/fbdisconnect')
def fbdisconnect():
  facebook_id = login_session['facebook_id']
  url = 'https://graph.facebook.com/{}/permissions'.format(facebook_id)
  h = httplib2.Http()
  h.request(url, 'DELETE')[1]


@app.route('/gconnect', methods=['POST'])
def gconnect():
    secret_file = os.path.join(base_dir, 'oauth_credentials/client_secret_google.json')
    client_id = json.loads(open(secret_file, 'r').read())['web']['client_id']

    # Validate state token
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    # Obtain authorization code
    code = request.data
    try:
        # Upgrade the authorization code into a credentials object
        secret_file = os.path.join(base_dir, 'oauth_credentials/client_secret_google.json')
        oauth_flow = flow_from_clientsecrets(secret_file, scope='')
        oauth_flow.redirect_uri = 'postmessage'
        credentials = oauth_flow.step2_exchange(code)
    except FlowExchangeError:
        response = make_response(
            json.dumps('Failed to upgrade the authorization code.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Check that the access token is valid.
    access_token = credentials.access_token
    url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s'
           % access_token)
    h = httplib2.Http()
    result = json.loads(h.request(url, 'GET')[1])
    # If there was an error in the access token info, abort.
    if result.get('error') is not None:
        response = make_response(json.dumps(result.get('error')), 500)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is used for the intended user.
    gplus_id = credentials.id_token['sub']
    if result['user_id'] != gplus_id:
        response = make_response(
            json.dumps("Token's user ID doesn't match given user ID."), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is valid for this app.

    if result['issued_to'] != client_id:
        response = make_response(
            json.dumps("Token's client ID does not match app's."), 401)
        print "Token's client ID does not match app's."
        response.headers['Content-Type'] = 'application/json'
        return response

    stored_credentials = login_session.get('credentials')
    stored_gplus_id = login_session.get('gplus_id')
    if stored_credentials is not None and gplus_id == stored_gplus_id:
        response = make_response(json.dumps('Current user is already connected.'),
                                 200)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Store the access token in the session for later use.
    login_session['provider'] = 'google'
    login_session['credentials'] = credentials.access_token
    login_session['gplus_id'] = gplus_id

    # Get user info and populate login_session
    userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
    params = {'access_token': credentials.access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)

    data = answer.json()
    login_session['username'] = data['name']
    login_session['picture'] = data['picture']
    login_session['email'] = data['email']

    # Check if user exists, if not create one.
    user_id = db_controller.get_user_id_from_email(login_session['email'])
    if not user_id:
      user_id = db_controller.create_user_from_session(login_session)
    login_session['user_id'] = user_id
    output = ''
    output += '<h1>Welcome, '
    output += login_session['username']
    output += '!</h1>'
    output += '<img src="'
    output += login_session['picture']
    output += ' " style = "width: 300px; height: 300px;border-radius: 150px;-webkit-border-radius: 150px;-moz-border-radius: 150px;"> '
    flash("You are now logged in as %s" % login_session['username'])
    return output

# Disconnect - Revoke a current user's token and reset their login_session.
@app.route('/gdisconnect')
def gdisconnect():
  # Only disconnect a connected user.
  credentials = login_session.get('credentials')
  if credentials is None:
    response = make_response(json.dumps('Current user not connected.'), 401)
    response.headers['Content-Type'] = 'application/json'
    return response

  # Execute HHTP GET request to revoke current token.
  access_token = credentials
  url = 'https://accounts.google.com/o/oauth2/revoke?token=%s' % access_token
  h = httplib2.Http()
  result = h.request(url, 'GET')[0]

  if result['status'] == '200':
    response = make_response(json.dumps('Successfully disconnected.'), 200)
    response.headers['Content-Type'] = 'application/json'
    return response
  else:
    # If for some reason the token was invalid.
    response = make_response(json.dumps('Failed to revoke token for given user.', 400))
    response.headers['Content-Type'] = 'application/json'
    return response


# LOGIN
# Create anti-forgery state token
@app.route('/login')
def showLogin():
    secret_file = os.path.join(base_dir, 'oauth_credentials/client_secret_google.json')
    google_client_id = json.loads(open(secret_file, 'r').read())['web']['client_id']
    fb_secret_file = os.path.join(base_dir, 'oauth_credentials/client_secret_fb.json')
    fb_app_id = json.loads(open(fb_secret_file, 'r').read())['web']['app_id']

    state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                    for x in xrange(32))
    login_session['state'] = state
    return render_template('login.html', STATE = state,
                            google_client_id = google_client_id, fb_app_id = fb_app_id)


##### HOME ROUTE #####
@app.route('/')
@app.route('/books')
def showBooksFront():
    categories = db_controller.get_categories()
    books = db_controller.get_recent_books(5)

    return render_template('front.html', categories = categories,
                            books = books)


##### CATEGORY ROUTES #####

# ADD A CATEGORY
@app.route('/books/categories/add', methods=['GET', 'POST'])
def addBookCategory():

    # Redirect to login if user is not logged in.
    if 'username' not in login_session:
        flash('Please login first')
        return redirect('/login')
    if request.method == "GET":
        return render_template('addBookCategory.html')
    if request.method == 'POST':
        cat_name = request.form['name']
        category_exists = db_controller.category_exists(cat_name)

        # Check if the category already exists.
        if category_exists:
            flash("Sorry, the category '{}' already exists.".format(cat_name))
            return redirect(url_for('addBookCategory'))
        else:
            new_category = db_controller.create_category(cat_name)
            flash("A new category named '{}' has been created".format(new_category.name))
            return redirect(url_for('showBookCategory', book_cat_id = new_category.id))


# SHOW A CATEGORY
@app.route('/books/categories/<int:book_cat_id>')
def showBookCategory(book_cat_id):
    current_category = db_controller.get_category(book_cat_id)
    books = db_controller.get_books_by_category(book_cat_id)

    return render_template('showBookCategory.html',
                            current_category = current_category,
                            books = books)


# EDIT A CATEGORY
@app.route('/books/categories/<int:book_cat_id>/edit', methods=['GET', 'POST'])
def editBookCategory(book_cat_id):
    category = db_controller.get_category(book_cat_id)

    # Redirect to login if user is not logged in.
    if 'username' not in login_session:
        flash('Please login first')
        return redirect('/login')

    if request.method == 'GET':
        return render_template('editBookCategory.html', category = category)
    if request.method == 'POST':
        category = db_controller.update_category(id = category.id,
                                                 name = request.form['name'])
        flash("The category '{}' has been updated.".format(category.name))
        return redirect(url_for('showBookCategory', book_cat_id = category.id))


# DELETE A CATEGORY
@app.route('/books/categories/<int:book_cat_id>/delete', methods=['GET', 'POST'])
def deleteBookCategory(book_cat_id):
    category = db_controller.get_category(book_cat_id)

    # Redirect to login if user is not logged in.
    if 'username' not in login_session:
        flash('Please login first')
        return redirect('/login')

    if request.method == 'GET':
        return render_template('deleteBookCategory.html', category = category)
    if request.method == 'POST':
        deleted_category = db_controller.delete_category(category.id)
        flash("The category '{}' has been deleted.".format(deleted_category.name))
        return redirect(url_for('showBooksFront'))


##### BOOK ROUTES #####

# ADD A BOOK
@app.route('/books/add', methods=['GET', 'POST'])
def addBook():

    # Redirect to login if user is not logged in.
    if 'username' not in login_session:
        flash('You must first login before creating a new book')
        return redirect('/login')

    if request.method == 'GET':
        return render_template('addBook.html')
    if request.method == 'POST':
        book = db_controller.create_book(
            name = request.form['name'],
            author = request.form['author'],
            description = request.form['description'],
            price = request.form['price'],
            image = request.form['image'],
            category_id = request.form['category'],
            user_id = login_session['user_id'])
        flash("A new book named '{}' has been created.".format(book.name))
        return redirect(url_for('showBook', book_id = book.id))


# SHOW A BOOK
@app.route('/books/<int:book_id>')
def showBook(book_id):
    book = db_controller.get_book(book_id)

    return render_template('showBook.html', book = book)


# EDIT A BOOK
@app.route('/books/<int:book_id>/edit', methods=['GET', 'POST'])
def editBook(book_id):
    book = db_controller.get_book(book_id)

    # Redirect if user is not logged in or creator of book.
    if 'username' not in login_session:
        flash('Please login first!')
        return redirect('/login')
    if login_session['user_id'] != book.user_id:
        flash('Only the user who created a book can edit it!')
        return redirect(url_for('showBook', book_id = book.id))

    if request.method == 'GET':
        return render_template('editBook.html', book = book)

    if request.method == 'POST':
        book = db_controller.update_book(
            book_id = book.id,
            name = request.form['name'],
            author = request.form['author'],
            description = request.form['description'],
            price = request.form['price'],
            image = request.form['image'],
            category_id = request.form['category'])
        flash("The book named '{}' has been updated".format(book.name))
        return redirect(url_for('showBook', book_id = book.id))


# DELETE A BOOK
@app.route('/books/<int:book_id>/delete', methods=['GET', 'POST'])
def deleteBook(book_id):
    book = db_controller.get_book(book_id)

    # Redirect if user is not logged in or creator of book.
    if 'username' not in login_session:
        flash('Please login first')
        return redirect('/login')
    if login_session['user_id'] != book.user_id:
        flash('Only the user who created a book can edit it!')
        return redirect(url_for('showBook', book_id = book.id))

    if request.method == 'GET':
        return render_template('deleteBook.html', book = book)
    if request.method == 'POST':
        book = db_controller.delete_book(book.id)
        flash("The book named '{}' has been deleted".format(book.name))
        return redirect(url_for('showBooksFront'))

##### USER ROUTES #####
@app.route('/users/<int:user_id>')
def showUser(user_id):
    user = db_controller.get_user(user_id)

    return render_template('showUser.html', user = user)


##### API ENDPOINTS #####

# RETURN JSON FOR AN INDIVIDUAL BOOK
@app.route('/books/<int:book_id>/JSON')
def bookJSON(book_id):
    book = db_controller.get_book(book_id)
    return jsonify(book = book.serialize)


# RETURN JSON FOR ALL BOOKS
@app.route('/books/JSON')
def booksJSON():
    books = db_controller.get_books()
    return jsonify(books = [book.serialize for book in books])


# RETURN JSON FOR AN INDIVIDUAL CATEGORY
@app.route('/books/categories/<int:book_cat_id>/JSON')
def categoryJSON(book_cat_id):
    category = db_controller.get_category(book_cat_id)
    return jsonify(category = category.serialize)


# RETURN JSON FOR ALL CATEGORIES
@app.route('/books/categories/JSON')
def categoriesJSON():
    categories = db_controller.get_categories()
    return jsonify(categories = [cat.serialize for cat in categories])


# RETURN JSON FOR AN INDIVIDUAL USER
@app.route('/users/<int:user_id>/JSON')
def userJSON(user_id):
    user = db_controller.get_user(user_id)
    return jsonify(user = user.serialize)


# RETURN JSON FOR ALL USERS
@app.route('/users/JSON')
def usersJSON():
    users = db_controller.get_users()
    return jsonify(users = [user.serialize for user in users])


##### ADMIN ROUTES #####
@app.route('/admin')
def adminMain():
    users = db_controller.get_users()
    categories = db_controller.get_categories()
    books = db_controller.get_books()

    return render_template('admin.html', users = users, categories = categories, books = books)

if __name__ == '__main__':
    app.debug = True
    app.secret_key = config.secret_key
    app.run(config.host, config.port)

