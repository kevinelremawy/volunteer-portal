import os
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, session, request, flash
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import distinct

# Load env vars
load_dotenv()

# App setup
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI']        = 'sqlite:///volunteer.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# OAuth setup
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

# DB setup
db = SQLAlchemy(app)

# Models
class Event(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    title       = db.Column(db.String(100), nullable=False)
    date        = db.Column(db.DateTime, nullable=False)
    description = db.Column(db.Text)
    category    = db.Column(db.String(50), nullable=False, default='General')

# ← Insert the Signup model here ↓
class Signup(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(200), nullable=False)
    event_id   = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)

class User(db.Model):
    email      = db.Column(db.String(200), primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    phone      = db.Column(db.String(20))
    pref_email = db.Column(db.Boolean, default=False)
    pref_sms   = db.Column(db.Boolean, default=False)
    task_prefs = db.Column(db.String(500), default='')  # comma-separated categories

# Routes
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/login')
def login():
    return google.authorize_redirect(url_for('auth', _external=True))

@app.route('/auth')
def auth():
    token = google.authorize_access_token()
    user = google.userinfo()
    session['user'] = user
    session['role'] = 'volunteer'
    return redirect(url_for('home'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('role', None)
    return redirect(url_for('home'))

@app.route('/volunteer')
def volunteer_dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))

    # 1) Load the DB User record
    email = session['user']['email']
    u = User.query.get(email)
    # If they’ve never visited profile, bootstrap a record
    if not u:
        u = User(email=email,
                 name=session['user'].get('name',''))
        db.session.add(u)
        db.session.commit()

    # 2) Fetch enriched signups
    raw_signups = Signup.query.filter_by(user_email=email).all()
    signups = []
    for s in raw_signups:
        ev = Event.query.get(s.event_id)
        if ev:
            signups.append({
                "event_id":    ev.id,
                "event_title": ev.title,
                "event_date":  ev.date
            })

    # 3) Fetch upcoming events
    events = Event.query.order_by(Event.date).all()

    return render_template(
        'volunteer.html',
        user=u,        # pass the DB user
        signups=signups,
        events=events
    )



@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == os.getenv('ADMIN_PASSWORD'):
            session['role'] = 'admin'
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Wrong password', 'error')
    return render_template('admin_login.html')



@app.route('/admin')
def admin_dashboard():
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))

    # Fetch all events for listing & lookup
    events = {e.id: e for e in Event.query.order_by(Event.date).all()}

    # Build a list of signups with names and event titles
    signup_records = []
    for s in Signup.query.all():
        user = User.query.filter_by(email=s.user_email).first()
        ev   = events.get(s.event_id)
        signup_records.append({
            "name": user.name if user else s.user_email,
            "email": s.user_email,
            "event": ev.title if ev else "(deleted)",
        })

    return render_template(
        'admin.html',
        events=events.values(),        # current events list
        signups=signup_records         # enriched sign-ups
    )


@app.route('/admin/delete-event/<int:event_id>', methods=['POST'])
def delete_event(event_id):
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))
    ev = Event.query.get_or_404(event_id)
    db.session.delete(ev)
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/signup/<int:event_id>', methods=['POST'])
def signup(event_id):
    # 1) Ensure user is logged in
    if 'user' not in session:
        flash("Please log in to sign up for events.", "warning")
        return redirect(url_for('login'))

    email = session['user']['email']
    # 2) Prevent duplicate sign-ups
    existing = Signup.query.filter_by(user_email=email, event_id=event_id).first()
    if existing:
        flash("You’re already signed up for that event.", "info")
    else:
        # 3) Create the signup
        db.session.add(Signup(user_email=email, event_id=event_id))
        db.session.commit()
        flash("You have successfully signed up!", "success")

    return redirect(url_for('events'))


@app.route('/admin/create-event', methods=['GET','POST'])
def create_event():
    if session.get('role')!='admin':
        return redirect(url_for('admin_login'))
    if request.method=='POST':
        title    = request.form['title']
        date     = datetime.fromisoformat(request.form['date'])
        desc     = request.form['description']
        category = request.form['category']
        ev = Event(title=title, date=date, description=desc, category=category)
        db.session.add(ev); db.session.commit()
        return redirect(url_for('events'))
    return render_template('create_event.html')

@app.route('/admin/edit-event/<int:event_id>', methods=['GET','POST'])
def edit_event(event_id):
    # only admins
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))
    ev = Event.query.get_or_404(event_id)

    if request.method == 'POST':
        # pull updated values from form
        ev.title       = request.form['title']
        ev.date        = datetime.fromisoformat(request.form['date'])
        ev.description = request.form['description']
        ev.category    = request.form['category']
        db.session.commit()
        flash("Event updated.", "success")
        return redirect(url_for('admin_dashboard'))

    # GET → render the form with current values
    return render_template('edit_event.html', event=ev)


@app.route('/events')
def events():
    # pull distinct categories for filter buttons
    categories = [c[0] for c in db.session.query(distinct(Event.category)).all()]
    selected   = request.args.get('category', 'All')

    q = Event.query
    if selected != 'All':
        q = q.filter_by(category=selected)
    events = q.order_by(Event.date).all()

    return render_template('events.html',
                           events=events,
                           categories=categories,
                           selected=selected)


    # get selected category from query string
    selected = request.args.get('category', 'All')

    q = Event.query
    if selected and selected != 'All':
        q = q.filter_by(category=selected)
    events = q.order_by(Event.date).all()

    return render_template('events.html',
                           events=events,
                           categories=categories,
                           selected=selected)

@app.route('/preferences', methods=['POST'])
def preferences():
    if 'user' not in session:
        return redirect(url_for('login'))
    # Save prefs in session for now
    session['pref_email'] = bool(request.form.get('pref_email'))
    session['pref_sms']   = bool(request.form.get('pref_sms'))
    return redirect(url_for('volunteer_dashboard'))


@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/profile', methods=['GET','POST'])
def profile():
    # 1) Require login
    if 'user' not in session:
        return redirect(url_for('login'))

    email = session['user']['email']
    # 2) Load or create the User record
    u = User.query.get(email)
    if not u:
        u = User(
            email=email,
            name=session['user'].get('name', '')
        )
        db.session.add(u)
        db.session.commit()

    # 3) Build category list from existing events
    categories = [
        c[0] for c in db.session.query(distinct(Event.category)).all()
    ]

    if request.method == 'POST':
        # 4) Update fields
        u.name       = request.form['name']
        u.phone      = request.form.get('phone', '')
        u.pref_email = bool(request.form.get('pref_email'))
        u.pref_sms   = bool(request.form.get('pref_sms'))
        # collect selected categories
        prefs = request.form.getlist('task_prefs')
        u.task_prefs = ",".join(prefs)
        db.session.commit()

        flash("Profile updated!", "success")
        return redirect(url_for('volunteer_dashboard'))

    # 5) Render form
    return render_template(
        'profile.html',
        user=u,
        categories=categories
    )

@app.route('/cancel/<int:event_id>', methods=['POST'])
def cancel(event_id):
    # Only signed-in users can cancel
    if 'user' not in session:
        return redirect(url_for('login'))

    email = session['user']['email']
    # Find and delete the signup
    signup = Signup.query.filter_by(user_email=email, event_id=event_id).first()
    if signup:
        db.session.delete(signup)
        db.session.commit()
        flash("Your signup has been canceled.", "success")
    else:
        flash("You were not signed up for that event.", "warning")

    return redirect(url_for('volunteer_dashboard'))


if __name__ == "__main__":
    # Create tables if they don't exist
    with app.app_context():
        db.create_all()
    app.run(debug=True)
