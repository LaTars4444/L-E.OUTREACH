import os
import re
import base64
import time
import random
import stripe
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from pypdf import PdfReader
import google_auth_oauthlib.flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

# --- SECURE CONFIGURATION (Uses Render Environment Variables) ---
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
PRICE_ID_WEEKLY = os.environ.get("PRICE_ID_WEEKLY")
PRICE_ID_FOREVER = os.environ.get("PRICE_ID_FOREVER")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.environ.get("REDIRECT_URI")

GOOGLE_CLIENT_CONFIG = {
    "web": {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [REDIRECT_URI]
    }
}

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "default_secret_123")
app.config['UPLOAD_FOLDER'] = '/tmp'

# --- BACKEND HELPERS ---

def find_emails_in_text(text):
    text = text.lower()
    for tld in ['.com', '.net', '.org', '.edu', '.co.uk']:
        text = text.replace(tld, f"{tld} ")
    return re.findall(r'[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}', text)

def extract_from_pdf(filepath):
    found = set()
    try:
        reader = PdfReader(filepath)
        for page in reader.pages:
            found.update(find_emails_in_text(page.extract_text() or ""))
    except: pass
    return list(found)

def send_gmail(creds, to, sub, body, attachment_path=None):
    try:
        service = build('gmail', 'v1', credentials=Credentials(**creds))
        message = MIMEMultipart()
        message['to'] = to
        message['subject'] = sub
        message.attach(MIMEText(body))

        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, 'rb') as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
            message.attach(part)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        return True
    except: return False

# --- UI TEMPLATE ---

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Outreach System</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen flex items-center justify-center p-4">
    <div class="w-full max-w-xl bg-white rounded-[2.5rem] shadow-2xl border border-gray-100 p-8 md:p-12">
        
        <header class="text-center mb-10">
            <h1 class="text-5xl font-black italic underline decoration-4 mb-2">OUTREACH</h1>
            <p class="text-gray-400 text-xs font-bold uppercase tracking-widest">Premium Automation System</p>
        </header>

        {% with m = get_flashed_messages() %}
            {% if m %}<div class="mb-6 p-4 bg-black text-white rounded-2xl text-sm font-bold text-center">{{ m[0]|safe }}</div>{% endif %}
        {% endwith %}

        {% if not is_logged_in %}
            <div class="text-center py-12">
                <a href="/login" class="inline-block bg-black text-white px-10 py-5 rounded-2xl font-black text-lg hover:opacity-80 transition shadow-lg">Sign in with Google</a>
            </div>
        {% elif not has_paid %}
            <div class="text-center space-y-6">
                <div class="bg-amber-50 p-6 rounded-3xl border border-amber-100">
                    <h2 class="text-xl font-black text-amber-900 mb-1">System Locked</h2>
                    <p class="text-amber-700 text-sm">Choose a plan to start your campaign.</p>
                </div>
                
                <div class="grid md:grid-cols-2 gap-4">
                    <form action="/create-checkout-session" method="POST">
                        <input type="hidden" name="plan" value="weekly">
                        <button type="submit" class="w-full p-6 border-2 border-black rounded-3xl hover:bg-gray-50 transition text-left">
                            <span class="block text-2xl font-black">$3</span>
                            <span class="block text-xs font-bold uppercase text-gray-400">Per Week</span>
                            <span class="block mt-4 text-[10px] font-black bg-black text-white px-2 py-1 rounded inline-block">SUBSCRIBE</span>
                        </button>
                    </form>

                    <form action="/create-checkout-session" method="POST">
                        <input type="hidden" name="plan" value="forever">
                        <button type="submit" class="w-full p-6 bg-black text-white rounded-3xl hover:opacity-90 transition text-left">
                            <span class="block text-2xl font-black">$20</span>
                            <span class="block text-xs font-bold uppercase text-gray-500">Forever</span>
                            <span class="block mt-4 text-[10px] font-black bg-white text-black px-2 py-1 rounded inline-block">LIFETIME</span>
                        </button>
                    </form>
                </div>
            </div>
        {% else %}
            <form action="/process" method="POST" enctype="multipart/form-data" class="space-y-6">
                <div>
                    <label class="block text-[10px] font-black uppercase text-gray-400 mb-2 ml-1">1. Recipients (Text or PDF Extract)</label>
                    <textarea name="manual_emails" placeholder="Paste emails here..." class="w-full border p-4 rounded-2xl h-24 text-sm font-mono focus:ring-2 ring-black outline-none mb-2"></textarea>
                    <input type="file" name="extract_file" accept=".pdf" class="block w-full text-xs text-gray-400 border border-dashed p-3 rounded-xl">
                </div>

                <div>
                    <label class="block text-[10px] font-black uppercase text-gray-400 mb-2 ml-1">2. Email Content</label>
                    <input type="text" name="subject" placeholder="Subject Line" class="w-full border p-4 rounded-2xl text-sm focus:ring-2 ring-black outline-none mb-2" required>
                    <textarea name="body" placeholder="Message body..." class="w-full border p-4 rounded-2xl h-32 text-sm focus:ring-2 ring-black outline-none" required></textarea>
                </div>

                <div>
                    <label class="block text-[10px] font-black uppercase text-gray-400 mb-2 ml-1">3. PDF Attachment (Optional)</label>
                    <input type="file" name="attachment_file" accept=".pdf" class="block w-full text-sm file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-xs file:font-black file:bg-black file:text-white">
                </div>

                <button type="submit" class="w-full bg-black text-white py-6 rounded-3xl font-black text-xl shadow-2xl hover:bg-gray-900 transition-all">Launch Campaign 🚀</button>
            </form>
            <div class="mt-8 text-center"><a href="/logout" class="text-[10px] font-black uppercase text-gray-300 hover:text-black transition">Logout</a></div>
        {% endif %}
    </div>
</body>
</html>
"""

# --- ROUTES ---

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, is_logged_in=('credentials' in session), has_paid=session.get('has_paid', False))

@app.route('/login')
def login():
    flow = google_auth_oauthlib.flow.Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=['https://www.googleapis.com/auth/gmail.send'])
    flow.redirect_uri = REDIRECT_URI
    auth_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true')
    session['state'] = state
    return redirect(auth_url)

@app.route('/callback')
def callback():
    flow = google_auth_oauthlib.flow.Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=['https://www.googleapis.com/auth/gmail.send'], state=session.get('state'))
    flow.redirect_uri = REDIRECT_URI
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    session['credentials'] = {'token': creds.token, 'refresh_token': creds.refresh_token, 'token_uri': creds.token_uri, 'client_id': creds.client_id, 'client_secret': creds.client_secret, 'scopes': creds.scopes}
    return redirect(url_for('index'))

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout():
    plan = request.form.get('plan')
    price_id = PRICE_ID_WEEKLY if plan == 'weekly' else PRICE_ID_FOREVER
    mode = 'subscription' if plan == 'weekly' else 'payment'
    
    try:
        checkout_session = stripe.checkout.Session.create(
            line_items=[{'price': price_id, 'quantity': 1}],
            mode=mode,
            success_url=request.host_url + 'payment-success',
            cancel_url=request.host_url,
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e: return str(e)

@app.route('/payment-success')
def payment_success():
    session['has_paid'] = True
    flash("Access Granted: System Unlocked ✅")
    return redirect(url_for('index'))

@app.route('/process', methods=['POST'])
def process():
    if 'credentials' not in session or not session.get('has_paid'): return redirect(url_for('index'))
    
    emails = set(find_emails_in_text(request.form.get('manual_emails', '')))
    f_ext = request.files.get('extract_file')
    if f_ext and f_ext.filename.endswith('.pdf'):
        p = os.path.join('/tmp', 'e_' + secure_filename(f_ext.filename))
        f_ext.save(p)
        emails.update(extract_from_pdf(p))
    
    f_att = request.files.get('attachment_file')
    att_path = None
    if f_att and f_att.filename.endswith('.pdf'):
        att_path = os.path.join('/tmp', 'a_' + secure_filename(f_att.filename))
        f_att.save(att_path)

    targets = list(emails)
    if not targets:
        flash("Error: No recipients found.")
        return redirect(url_for('index'))

    count = 0
    for i, email in enumerate(targets):
        if send_gmail(session['credentials'], email, request.form.get('subject'), request.form.get('body'), att_path):
            count += 1
        if i < len(targets) - 1: time.sleep(random.randint(5, 12))
   
    flash(f"Campaign Complete: {count} emails sent.")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
