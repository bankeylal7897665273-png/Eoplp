from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import requests
import os
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
import random
import datetime

load_dotenv()

app = Flask(__name__)
app.secret_key = 'bankey_otp_secret_key_full_vip'

FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY")
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@gmail.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# Firebase Auth REST API URLs
AUTH_SIGNUP_URL = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_API_KEY}"
AUTH_LOGIN_URL = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"

def db_put(path, data):
    requests.put(f"{FIREBASE_DB_URL}/otp_bot/{path}.json", json=data)

def db_patch(path, data):
    requests.patch(f"{FIREBASE_DB_URL}/otp_bot/{path}.json", json=data)

def db_get(path):
    res = requests.get(f"{FIREBASE_DB_URL}/otp_bot/{path}.json")
    return res.json() if res and res.status_code == 200 else None

@app.route('/')
def auth_page():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('auth.html')

@app.route('/api/auth', methods=['POST'])
def authenticate():
    data = request.json
    action = data.get('action')
    email = data.get('email')
    password = data.get('password')
    username = data.get('username', '').strip()

    payload = {"email": email, "password": password, "returnSecureToken": True}
    
    try:
        if action == 'register':
            # Check if username exists
            users = db_get("users") or {}
            for uid, udata in users.items():
                if udata.get('username') == username:
                    return jsonify({"status": "error", "message": "Username already taken!"})

            res = requests.post(AUTH_SIGNUP_URL, json=payload)
            if res.status_code == 200:
                user_id = res.json()['localId']
                # Create user profile in DB
                db_put(f"users/{user_id}", {
                    "email": email,
                    "username": username,
                    "plan": "Free",
                    "otp_limit": 0,
                    "otp_sent": 0,
                    "otp_failed": 0,
                    "api_status": "inactive"
                })
                return jsonify({"status": "success", "message": "Account Created Successfully!"})
            else:
                return jsonify({"status": "error", "message": res.json()['error']['message']})
                
        elif action == 'login':
            res = requests.post(AUTH_LOGIN_URL, json=payload)
            if res.status_code == 200:
                user_id = res.json()['localId']
                session['user_id'] = user_id
                # Check if admin
                if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
                    session['is_admin'] = True
                return jsonify({"status": "success", "message": "Login Successful!"})
            else:
                return jsonify({"status": "error", "message": "Invalid Email or Password!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('auth_page'))
    user_id = session['user_id']
    user_data = db_get(f"users/{user_id}")
    history = db_get(f"history/{user_id}") or {}
    return render_template('dashboard.html', user=user_data, history=history)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth_page'))

@app.route('/api/submit_utr', methods=['POST'])
def submit_utr():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Not logged in"})
    
    data = request.json
    utr = data.get('utr')
    plan_name = data.get('plan_name')
    otp_amount = data.get('otp_amount')
    price = data.get('price')
    user_id = session['user_id']
    
    if len(utr) != 12:
        return jsonify({"status": "error", "message": "UTR must be 12 digits!"})
        
    db_put(f"pending_payments/{utr}", {
        "user_id": user_id,
        "plan_name": plan_name,
        "otp_amount": otp_amount,
        "price": price,
        "status": "pending",
        "date": str(datetime.datetime.now())
    })
    return jsonify({"status": "success", "message": "Request sent to Admin! Wait for approval."})

@app.route('/api/create_api', methods=['POST'])
def create_api():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Not logged in"})
        
    data = request.json
    name = data.get('name')
    app_email = data.get('app_email')
    app_password = data.get('app_password')
    user_id = session['user_id']
    
    if len(app_password) != 16:
        return jsonify({"status": "error", "message": "App Password must be 16 characters!"})
        
    user_data = db_get(f"users/{user_id}")
    if user_data.get('plan') == 'Free' and user_data.get('api_status') == 'active':
        return jsonify({"status": "error", "message": "Free users can only create API once!"})
        
    db_patch(f"users/{user_id}", {
        "api_name": name,
        "app_email": app_email,
        "app_password": app_password,
        "api_status": "active"
    })
    
    # If free plan, give 15 limit
    if user_data.get('plan') == 'Free':
         db_patch(f"users/{user_id}", {"otp_limit": 15})

    return jsonify({"status": "success", "message": "API Created Successfully!"})

# The Main API Endpoint for Users
@app.route('/<username>/<target_email>')
def send_user_otp(username, target_email):
    users = db_get("users") or {}
    user_id = None
    user_data = None
    
    for uid, udata in users.items():
        if udata.get('username') == username:
            user_id = uid
            user_data = udata
            break
            
    if not user_data:
        return jsonify({"status": "error", "message": "Invalid Username API"})
        
    if user_data.get('api_status') != 'active':
        return jsonify({"status": "error", "message": "API not created or inactive"})
        
    limit = user_data.get('otp_limit', 0)
    sent = user_data.get('otp_sent', 0)
    
    if str(limit) != "Unlimited" and sent >= int(limit):
        return jsonify({"status": "error", "message": "OTP Limit Reached! Upgrade Plan."})
        
    otp = str(random.randint(100000, 999999))
    sender_email = user_data.get('app_email')
    app_password = user_data.get('app_password')
    
    msg = MIMEText(f"Your OTP is: {otp}\n\nPowered by VIP OTP API System ☠️")
    msg['Subject'] = 'Verification OTP'
    msg['From'] = sender_email
    msg['To'] = target_email
    
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(sender_email, app_password)
        server.send_message(msg)
        server.quit()
        
        # Update DB
        db_patch(f"users/{user_id}", {"otp_sent": sent + 1})
        # Save History
        history_id = str(random.randint(10000, 99999))
        db_put(f"history/{user_id}/{history_id}", {
            "email": target_email,
            "otp": otp,
            "status": "Success",
            "date": str(datetime.datetime.now())
        })
        return jsonify({"status": "success", "message": "OTP Sent Successfully", "otp": otp})
        
    except Exception as e:
        failed = user_data.get('otp_failed', 0)
        db_patch(f"users/{user_id}", {"otp_failed": failed + 1})
        return jsonify({"status": "error", "message": "Failed to send OTP. Check App Password."})

# Admin Panel
@app.route('/admin')
def admin_panel():
    if not session.get('is_admin'):
        return "Access Denied", 403
    payments = db_get("pending_payments") or {}
    return render_template('admin.html', payments=payments)

@app.route('/admin/approve/<utr>')
def approve_utr(utr):
    if not session.get('is_admin'):
        return "Access Denied", 403
        
    payment = db_get(f"pending_payments/{utr}")
    if payment:
        user_id = payment['user_id']
        db_patch(f"users/{user_id}", {
            "plan": payment['plan_name'],
            "otp_limit": payment['otp_amount']
        })
        db_put(f"pending_payments/{utr}/status", "Approved")
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
