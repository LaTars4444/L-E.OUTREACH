import os, re, base64, time, random, stripe, sqlite3
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from pypdf import PdfReader
import google_auth_oauthlib.flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

# --- CONFIG ---
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
PRICE_ID_WEEKLY = os.environ.get("PRICE_ID_WEEKLY")
PRICE_ID_FOREVER = os.environ.get("PRICE_ID_FOREVER")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.environ.get("REDIRECT_URI")

GOOGLE_CLIENT_CONFIG = {"web": {"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": [REDIRECT_URI]}}

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "prod_key_999")
app.config['UPLOAD_FOLDER'] = '/tmp'
DB_PATH = "/tmp/outreach_v4.db"

def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, has_paid INTEGER DEFAULT 0, trial_end TEXT)")
        conn.commit()

init_db()

def get_user_email():
    if 'credentials' in session:
        try:
            creds = Credentials(**session['credentials'])
            info = build('oauth2', 'v2', credentials=creds).userinfo().get().execute()
            return info.get('email')
        except: return None
    return None

def check_access(email):
    if not email: return False
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user: return False
        if user['has_paid'] == 1: return True
        if user['trial_end'] and datetime.now() < datetime.fromisoformat(user['trial_end']): return True
    return False

# --- LOGIC FUNCTIONS ---
def find_emails_in_text(text):
    text = text.lower()
    for tld in ['.com', '.net', '.org', '.edu', '.co.uk']: text = text.replace(tld, f"{tld} ")
    return re.findall(r'[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}', text)

def extract_from_pdf(filepath):
    found = set()
    try:
        reader = PdfReader(filepath)
        for page in reader.pages: found.update(find_emails_in_text(page.extract_text() or ""))
    except: pass
    return list(found)

def send_gmail(creds, to, sub, body, attachment_path=None):
    try:
        service = build('gmail', 'v1', credentials=Credentials(**creds))
        msg = MIMEMultipart(); msg['to'], msg['subject'] = to, sub
        msg.attach(MIMEText(body))
        if attachment_path:
            with open(attachment_path, 'rb') as f:
                p = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
            p['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
            msg.attach(p)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        return True
    except: return False

# --- UI TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Outreach System</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-50 min-h-screen flex flex-col items-center justify-center p-4">
    <div class="w-full max-w-xl bg-white rounded-[2.5rem] shadow-2xl p-8 md:p-12 border border-gray-100">
        <header class="text-center mb-10">
            <h1 class="text-5xl font-black italic underline decoration-4 mb-2">OUTREACH</h1>
            <p class="text-gray-400 text-[10px] font-bold uppercase tracking-widest italic">Beta Production</p>
        </header>

        {% with m = get_flashed_messages() %}{% if m %}<div class="mb-6 p-4 bg-black text-white rounded-2xl text-sm font-bold text-center">{{ m[0]|safe }}</div>{% endif %}{% endwith %}

        {% if not is_logged_in %}
            <!-- ONLY LOGIN BUTTON SHOWN AT FIRST -->
            <div class="text-center py-12">
                <a href="/login" class="inline-block bg-black text-white px-12 py-6 rounded-3xl font-black text-xl shadow-xl hover:scale-105 transition-all">Sign in with Google</a>
                <p class="mt-4 text-gray-400 text-[10px] uppercase font-bold tracking-widest">Login to unlock free trial</p>
            </div>
        {% elif not access_valid %}
            <!-- TRIAL BUTTON ABOVE PAYMENT GRID -->
            <div class="text-center space-y-6">
                <div class="bg-blue-50 p-4 rounded-2xl border border-blue-100 text-blue-700 text-xs font-bold">Logged in as: {{ user_email }}</div>
                
                <a href="/start-trial" class="block w-full bg-blue-600 text-white py-5 rounded-2xl font-black text-lg shadow-lg hover:bg-blue-700 transition">Start 24-Hour Free Trial (No Card)</a>
                
                <div class="grid md:grid-cols-2 gap-4">
                    <form action="/create-checkout-session" method="POST"><input type="hidden" name="plan" value="weekly"><button class="w-full p-6 border-2 border-black rounded-3xl font-black hover:bg-gray-50 transition text-left"><span class="block text-2xl font-black">$3</span><span class="text-xs text-gray-400 uppercase">Per Week</span></button></form>
                    <form action="/create-checkout-session" method="POST"><input type="hidden" name="plan" value="forever"><button class="w-full p-6 bg-black text-white rounded-3xl font-black hover:opacity-90 transition text-left"><span class="block text-2xl font-black">$20</span><span class="text-xs text-gray-500 uppercase">Forever</span></button></form>
                </div>
            </div>
        {% else %}
            <!-- CAMPAIGN FORM (ONCE ACCESS IS GRANTED) -->
            <form action="/process" method="POST" enctype="multipart/form-data" class="space-y-4">
                <div class="flex justify-between items-center text-[10px] font-black uppercase text-gray-400"><span>1. Recipients</span><span class="text-green-500 font-bold italic">Access Active ✅</span></div>
                <textarea name="manual_emails" placeholder="Paste emails..." class="w-full border p-4 rounded-2xl h-24 text-sm font-mono focus:ring-2 ring-black outline-none mb-1"></textarea>
                <input type="file" name="extract_file" accept=".pdf" class="block w-full text-[10px] border border-dashed p-3 rounded-xl mb-4">
                
                <input type="text" name="subject" placeholder="Subject" class="w-full border p-4 rounded-xl text-sm outline-none focus:ring-2 ring-black" required>
                <textarea name="body" placeholder="Message content..." class="w-full border p-4 rounded-xl h-32 text-sm outline-none focus:ring-2 ring-black" required></textarea>
                
                <div class="p-4 bg-gray-50 rounded-2xl border border-gray-200"><label class="block text-[10px] font-black uppercase text-gray-400 mb-2">3. Attachment (PDF)</label><input type="file" name="attachment_file" accept=".pdf" class="block w-full text-xs"></div>
                
                <button type="submit" class="w-full bg-black text-white py-6 rounded-3xl font-black text-xl shadow-2xl mt-4 hover:bg-gray-900">Launch Campaign 🚀</button>
            </form>
            <div class="mt-8 text-center"><a href="/logout" class="text-[10px] font-black uppercase text-gray-300 hover:text-red-500 transition">Logout Session</a></div>
        {% endif %}
        
        <footer class="mt-10 text-center space-x-4"><a href="/privacy" class="text-[9px] text-gray-400 uppercase font-bold hover:text-black">Privacy Policy</a><a href="/terms" class="text-[9px] text-gray-400 uppercase font-bold hover:text-black">Terms of Use</a></footer>
    </div>
</body>
</html>
"""

# --- ROUTES ---
@app.route('/')
def index():
    email = get_user_email()
    return render_template_string(HTML_TEMPLATE, is_logged_in=('credentials' in session), access_valid=check_access(email), user_email=email)

@app.route('/login')
def login():
    flow = google_auth_oauthlib.flow.Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=['https://www.googleapis.com/auth/gmail.send', 'https://www.googleapis.com/auth/userinfo.email', 'openid'])
    flow.redirect_uri = REDIRECT_URI
    auth_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true')
    session['state'] = state
    return redirect(auth_url)

@app.route('/callback')
def callback():
    flow = google_auth_oauthlib.flow.Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=['https://www.googleapis.com/auth/gmail.send', 'https://www.googleapis.com/auth/userinfo.email', 'openid'], state=session.get('state'))
    flow.redirect_uri = REDIRECT_URI
    flow.fetch_token(authorization_response=request.url)
    session['credentials'] = {'token': flow.credentials.token, 'refresh_token': flow.credentials.refresh_token, 'token_uri': flow.credentials.token_uri, 'client_id': flow.credentials.client_id, 'client_secret': flow.credentials.client_secret, 'scopes': flow.credentials.scopes}
    return redirect(url_for('index'))

@app.route('/start-trial')
def start_trial():
    email = get_user_email()
    if email:
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if not user or (not user['has_paid'] and not user['trial_end']):
                end_date = (datetime.now() + timedelta(hours=24)).isoformat()
                conn.execute("INSERT OR REPLACE INTO users (email, has_paid, trial_end) VALUES (?, 0, ?)", (email, end_date))
                conn.commit()
                flash("Trial Started! You have access for 24 hours.")
            else: flash("Trial already used or account active.")
    return redirect(url_for('index'))

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout():
    plan = request.form.get('plan')
    try:
        s = stripe.checkout.Session.create(customer_email=get_user_email(), line_items=[{'price': PRICE_ID_WEEKLY if plan == 'weekly' else PRICE_ID_FOREVER, 'quantity': 1}], mode=('subscription' if plan == 'weekly' else 'payment'), success_url=request.host_url + 'payment-success', cancel_url=request.host_url)
        return redirect(s.url, code=303)
    except Exception as e: return str(e)

@app.route('/payment-success')
def payment_success():
    email = get_user_email()
    if email:
        with get_db() as conn: conn.execute("INSERT OR REPLACE INTO users (email, has_paid) VALUES (?, 1)", (email,)); conn.commit()
        flash("Permanent Access Unlocked! ✅")
    return redirect(url_for('index'))

@app.route('/process', methods=['POST'])
def process():
    if not check_access(get_user_email()): return redirect(url_for('index'))
    emails = set(find_emails_in_text(request.form.get('manual_emails', '')))
    f_ext = request.files.get('extract_file')
    if f_ext and f_ext.filename.endswith('.pdf'):
        p = os.path.join('/tmp', 'e_' + secure_filename(f_ext.filename)); f_ext.save(p); emails.update(extract_from_pdf(p))
    f_att = request.files.get('attachment_file')
    att_path = None
    if f_att and f_att.filename.endswith('.pdf'):
        att_path = os.path.join('/tmp', 'a_' + secure_filename(f_att.filename)); f_att.save(att_path)
    targets = list(emails)
    if not targets: flash("Error: No emails found."); return redirect(url_for('index'))
    count = 0
    for i, e in enumerate(targets):
        if send_gmail(session['credentials'], e, request.form.get('subject'), request.form.get('body'), att_path): count += 1
        if i < len(targets) - 1: time.sleep(random.randint(5, 12))
    flash(f"Campaign Complete: {count} emails delivered.")
    return redirect(url_for('index'))

@app.route('/privacy')
def privacy(): return "Privacy: We use Google OAuth to send emails. No data is stored permanently."
@app.route('/terms')
def terms(): return "Terms: Beta software. Use at your own risk. No spamming allowed."
@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
