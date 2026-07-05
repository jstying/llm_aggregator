import logging

from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from auth import auth_bp
from auth.db import (
    FIREBASE_AVAILABLE,
    create_user,
    get_user_by_email,
    get_user_by_id,
    get_user_by_username,
)

logger = logging.getLogger(__name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    try:
        if not FIREBASE_AVAILABLE:
            flash('Authentication service unavailable.', 'error')
            return render_template('auth/login.html'), 503

        if request.method == 'POST':
            username = request.form['username'].strip()
            password = request.form['password']

            if not username or not password:
                flash('Username and password are required.', 'error')
                return render_template('auth/login.html')

            user = get_user_by_username(username)
            if user and check_password_hash(user['password_hash'], password):
                session['user_id'] = user['id']
                session['username'] = user['username']
                session.pop('is_guest', None)
                flash('Login successful!', 'success')
                return redirect(url_for('index'))

            flash('Invalid username or password.', 'error')

        return render_template('auth/login.html')
    except Exception as e:
        logger.error(f"Error in login: {e}", exc_info=True)
        flash('An unexpected error occurred.', 'error')
        return render_template('auth/login.html'), 500


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    try:
        if not FIREBASE_AVAILABLE:
            flash('Authentication service unavailable.', 'error')
            return render_template('auth/register.html'), 503

        if request.method == 'POST':
            username = request.form['username'].strip()
            email = request.form['email'].strip()
            password = request.form['password']
            confirm_password = request.form['confirm_password']

            if not username or not email or not password:
                flash('All fields are required.', 'error')
                return render_template('auth/register.html')

            if password != confirm_password:
                flash('Passwords do not match.', 'error')
                return render_template('auth/register.html')

            if len(password) < 6:
                flash('Password must be at least 6 characters.', 'error')
                return render_template('auth/register.html')

            if get_user_by_username(username):
                flash('Username already exists.', 'error')
                return render_template('auth/register.html')

            if get_user_by_email(email):
                flash('Email already registered.', 'error')
                return render_template('auth/register.html')

            user = create_user(username, email, password)
            session['user_id'] = user['id']
            session['username'] = user['username']
            session.pop('is_guest', None)
            flash('Registration successful! Welcome!', 'success')
            return redirect(url_for('index'))

        return render_template('auth/register.html')
    except Exception as e:
        logger.error(f"Error in register: {e}", exc_info=True)
        flash('Registration failed. Please try again.', 'error')
        return render_template('auth/register.html'), 500


@auth_bp.route('/logout')
def logout():
    try:
        session.pop('user_id', None)
        session.pop('username', None)
        session.pop('is_guest', None)
        flash('You have been logged out.', 'info')
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Error in logout: {e}", exc_info=True)
        return redirect(url_for('auth.login'))


@auth_bp.route('/profile')
def profile():
    try:
        if 'user_id' not in session:
            flash('Please login to view your profile.', 'error')
            return redirect(url_for('auth.login'))

        if not FIREBASE_AVAILABLE:
            flash('Authentication service unavailable.', 'error')
            return redirect(url_for('auth.login'))

        user = get_user_by_id(session['user_id'])
        if not user:
            session.pop('user_id', None)
            flash('User not found.', 'error')
            return redirect(url_for('auth.login'))

        return render_template('auth/profile.html', user=user)
    except Exception as e:
        logger.error(f"Error in profile: {e}", exc_info=True)
        flash('An unexpected error occurred.', 'error')
        return redirect(url_for('auth.login'))
