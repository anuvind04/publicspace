from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User
from django.utils import timezone
from .models import Post, Like, Comment, FriendRequest, Friendship, UserProfile
from django.db.models import Q
import random
import string
import razorpay
from django.conf import settings
from django.core.mail import send_mail
from django.utils.timezone import now
import pytz
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.units import inch
from io import BytesIO
from django.core.files.base import ContentFile
from django.utils.translation import gettext_lazy as _
from django.utils import translation
from django import forms


# ── Helper: get friend count ──
def get_friend_count(user):
    return Friendship.objects.filter(Q(user1=user) | Q(user2=user)).count()

# ── Helper: check post limit ──
def can_post(user):
    friend_count = get_friend_count(user)
    if friend_count == 0:
        return False, "You need at least 1 friend to post."
    if friend_count >= 10:
        return True, ""
    today_posts = Post.objects.filter(
        user=user,
        created_at__date=timezone.now().date()
    ).count()
    if today_posts >= friend_count:
        return False, f"You can only post {friend_count} time(s) per day. Add more friends to post more!"
    return True, ""

# ── Auth Views ──
def register_view(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        phone = request.POST.get('phone')
        email = request.POST.get('email')
        if form.is_valid():
            user = form.save(commit=False)
            user.email = email
            user.save()
            UserProfile.objects.create(user=user, phone=phone)
            login(request, user)
            return redirect('feed')
    else:
        form = UserCreationForm()
    return render(request, 'social/register.html', {'form': form})

def login_view(request):
    if request.method == 'POST':
        form = AuthenticationForm(data=request.POST)
        if form.is_valid():
            user = form.get_user()

            # Get user agent info
            import user_agents
            ua_string = request.META.get('HTTP_USER_AGENT', '')
            ua = user_agents.parse(ua_string)

            # Detect device type
            if ua.is_mobile:
                device_type = 'mobile'
            elif ua.is_tablet:
                device_type = 'tablet'
            else:
                device_type = 'desktop'

            # Get browser and OS
            browser = ua.browser.family
            os_name = ua.os.family

            # Get IP address
            ip = request.META.get('HTTP_X_FORWARDED_FOR', '')
            if ip:
                ip = ip.split(',')[0].strip()
            else:
                ip = request.META.get('REMOTE_ADDR', '')

            # ── Mobile time restriction (10AM - 1PM IST) ──
            if device_type == 'mobile':
                ist = pytz.timezone('Asia/Kolkata')
                current_time = now().astimezone(ist)
                if not (10 <= current_time.hour < 13):
                    # Save failed login attempt
                    from .models import LoginHistory
                    LoginHistory.objects.create(
                        user=user,
                        ip_address=ip,
                        browser=browser,
                        os=os_name,
                        device_type=device_type,
                        was_successful=False,
                    )
                    return render(request, 'social/login.html', {
                        'form': form,
                        'error': 'Mobile login is only allowed between 10:00 AM and 1:00 PM IST.'
                    })

            # ── Chrome OTP verification ──
            if 'Chrome' in browser and 'Chromium' not in browser and 'Edge' not in browser:
                # Generate OTP
                otp = generate_otp()
                from .models import OTPVerification
                OTPVerification.objects.filter(user=user, purpose='chrome_login').delete()
                OTPVerification.objects.create(user=user, otp=otp, purpose='chrome_login')

                # Send OTP email
                send_mail(
                    subject='PublicSpace — Login OTP Verification',
                    message=f'''Hi {user.username},

Your OTP for Chrome login verification is:

{otp}

This OTP is valid for 10 minutes.

Team PublicSpace
''',
                    from_email=settings.EMAIL_HOST_USER,
                    recipient_list=[user.email],
                    fail_silently=True,
                )

                # Store login data in session
                request.session['pending_login_user'] = user.id
                request.session['pending_login_ip'] = ip
                request.session['pending_login_browser'] = browser
                request.session['pending_login_os'] = os_name
                request.session['pending_login_device'] = device_type

                return render(request, 'social/chrome_otp.html', {
                    'email': user.email
                })

            # ── Normal login ──
            from .models import LoginHistory
            LoginHistory.objects.create(
                user=user,
                ip_address=ip,
                browser=browser,
                os=os_name,
                device_type=device_type,
                was_successful=True,
            )
            login(request, user)
            return redirect('feed')
    else:
        form = AuthenticationForm()
    return render(request, 'social/login.html', {'form': form})

def logout_view(request):
    logout(request)
    return redirect('login')

# ── Feed ──
@login_required
def feed(request):
    posts = Post.objects.all().order_by('-created_at')
    liked_posts = Like.objects.filter(user=request.user).values_list('post_id', flat=True)
    friend_count = get_friend_count(request.user)
    allowed, message = can_post(request.user)
    return render(request, 'social/feed.html', {
        'posts': posts,
        'liked_posts': liked_posts,
        'friend_count': friend_count,
        'can_post': allowed,
        'post_message': message,
    })

# ── Create Post ──
@login_required
def create_post(request):
    allowed, message = can_post(request.user)
    if not allowed:
        return redirect('feed')
    if request.method == 'POST':
        caption = request.POST.get('caption')
        image = request.FILES.get('image')
        video = request.FILES.get('video')
        Post.objects.create(user=request.user, caption=caption, image=image, video=video)
        return redirect('feed')
    return render(request, 'social/create_post.html')

# ── Like ──
@login_required
def like_post(request, pk):
    post = get_object_or_404(Post, pk=pk)
    like, created = Like.objects.get_or_create(user=request.user, post=post)
    if not created:
        like.delete()
    return redirect('feed')

# ── Comment ──
@login_required
def add_comment(request, pk):
    post = get_object_or_404(Post, pk=pk)
    if request.method == 'POST':
        text = request.POST.get('text')
        if text:
            Comment.objects.create(user=request.user, post=post, text=text)
    return redirect('feed')

# ── Delete Post ──
@login_required
def delete_post(request, pk):
    post = get_object_or_404(Post, pk=pk, user=request.user)
    if request.method == 'POST':
        post.delete()
    return redirect('feed')

# ── People & Friends ──
@login_required
def people(request):
    all_users = User.objects.exclude(id=request.user.id)
    sent = FriendRequest.objects.filter(from_user=request.user).values_list('to_user_id', flat=True)
    friends = Friendship.objects.filter(Q(user1=request.user) | Q(user2=request.user))
    friend_ids = []
    for f in friends:
        friend_ids.append(f.user2.id if f.user1 == request.user else f.user1.id)
    return render(request, 'social/people.html', {
        'all_users': all_users,
        'sent': sent,
        'friend_ids': friend_ids,
    })

@login_required
def send_request(request, pk):
    to_user = get_object_or_404(User, pk=pk)
    FriendRequest.objects.get_or_create(from_user=request.user, to_user=to_user)
    return redirect('people')

@login_required
def accept_request(request, pk):
    freq = get_object_or_404(FriendRequest, pk=pk, to_user=request.user)
    freq.accepted = True
    freq.save()
    Friendship.objects.get_or_create(user1=freq.from_user, user2=freq.to_user)
    freq.delete()
    return redirect('notifications')

@login_required
def notifications(request):
    requests = FriendRequest.objects.filter(to_user=request.user)
    return render(request, 'social/notifications.html', {'requests': requests})

# ── Password Generator (letters only) ──
def generate_password(length=10):
    characters = string.ascii_uppercase + string.ascii_lowercase
    return ''.join(random.choice(characters) for _ in range(length))

# ── Forgot Password ──
def forgot_password(request):
    error = None
    if request.method == 'POST':
        identifier = request.POST.get('identifier')
        user = None

        # Find by email
        user = User.objects.filter(email=identifier).first()

        # Find by phone if email not found
        if not user:
            profile = UserProfile.objects.filter(phone=identifier).first()
            if profile:
                user = profile.user
            else:
                error = "No account found with that email or phone number."

        if user:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            today = timezone.now().date()
            if profile.last_reset_request == today:
                error = "You can use this option only once per day."
            else:
                new_password = generate_password()
                user.set_password(new_password)
                user.save()
                profile.last_reset_request = today
                profile.save()
                return render(request, 'social/reset_success.html', {
                    'new_password': new_password,
                    'username': user.username,
                })

    return render(request, 'social/forgot_password.html', {'error': error})
# ── Subscription Plans ──
@login_required
def plans(request):
    from .models import Subscription, InternshipApplication
    subscription, _ = Subscription.objects.get_or_create(user=request.user)
    applications_this_month = InternshipApplication.objects.filter(
        user=request.user,
        applied_at__month=now().month,
        applied_at__year=now().year
    ).count()
    return render(request, 'social/plans.html', {
        'subscription': subscription,
        'applications_this_month': applications_this_month,
    })

# ── Create Razorpay Order ──
@login_required
def create_order(request, plan):
    ist = pytz.timezone('Asia/Kolkata')
    current_time = now().astimezone(ist)
    if not (10 <= current_time.hour < 11):
        return render(request, 'social/payment_blocked.html', {
            'current_time': current_time.strftime('%I:%M %p IST')
        })

    plan_prices = {
        'bronze': 10000,
        'silver': 30000,
        'gold': 100000,
    }

    if plan not in plan_prices:
        return redirect('plans')

    amount = plan_prices[plan]
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    order = client.order.create({
        'amount': amount,
        'currency': 'INR',
        'payment_capture': 1,
    })

    from .models import Payment
    payment = Payment.objects.create(
        user=request.user,
        razorpay_order_id=order['id'],
        plan=plan,
        amount=amount // 100,
    )

    return render(request, 'social/payment.html', {
        'order': order,
        'payment': payment,
        'plan': plan,
        'amount': amount // 100,
        'razorpay_key': settings.RAZORPAY_KEY_ID,
    })

# ── Payment Success ──
@login_required
def payment_success(request):
    if request.method == 'POST':
        razorpay_payment_id = request.POST.get('razorpay_payment_id')
        razorpay_order_id = request.POST.get('razorpay_order_id')

        from .models import Payment, Subscription
        import datetime

        try:
            payment = Payment.objects.get(razorpay_order_id=razorpay_order_id)
            payment.razorpay_payment_id = razorpay_payment_id
            payment.paid = True
            payment.save()

            subscription, _ = Subscription.objects.get_or_create(user=request.user)
            subscription.plan = payment.plan
            subscription.start_date = now().date()
            subscription.end_date = now().date() + datetime.timedelta(days=30)
            subscription.is_active = True
            subscription.save()

            send_mail(
                subject='PublicSpace — Payment Successful!',
                message=f'''Hi {request.user.username},

Your payment was successful!

Plan: {payment.plan.capitalize()}
Amount: ₹{payment.amount}
Payment ID: {razorpay_payment_id}
Valid until: {subscription.end_date}

Thank you for subscribing to PublicSpace!
''',
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[request.user.email],
                fail_silently=True,
            )

            return render(request, 'social/payment_success.html', {
                'payment': payment,
                'subscription': subscription,
            })
        except Payment.DoesNotExist:
            return redirect('plans')

    return redirect('plans')

# ── Apply for Internship ──
@login_required
def apply_internship(request):
    from .models import Subscription, InternshipApplication
    subscription, _ = Subscription.objects.get_or_create(user=request.user)
    applications_this_month = InternshipApplication.objects.filter(
        user=request.user,
        applied_at__month=now().month,
        applied_at__year=now().year
    ).count()

    limit = subscription.get_application_limit()
    if applications_this_month >= limit:
        return render(request, 'social/apply_internship.html', {
            'error': f'You have reached your limit of {limit} application(s) this month. Upgrade your plan to apply more!',
            'subscription': subscription,
        })

    if request.method == 'POST':
        company = request.POST.get('company')
        role = request.POST.get('role')
        if company and role:
            InternshipApplication.objects.create(
                user=request.user,
                company=company,
                role=role,
            )
            return redirect('my_applications')

    return render(request, 'social/apply_internship.html', {
        'subscription': subscription,
        'applications_this_month': applications_this_month,
        'limit': limit,
    })

# ── My Applications ──
@login_required
def my_applications(request):
    from .models import InternshipApplication
    applications = InternshipApplication.objects.filter(user=request.user).order_by('-applied_at')
    return render(request, 'social/my_applications.html', {'applications': applications})

# ── Generate OTP ──
def generate_otp():
    return str(random.randint(100000, 999999))

# ── Resume Form ──
@login_required
def resume_create(request):
    from .models import Subscription, Resume, OTPVerification

    subscription, _ = Subscription.objects.get_or_create(user=request.user)
    if subscription.plan == 'free':
        return render(request, 'social/resume_locked.html')

    existing_resume = Resume.objects.filter(user=request.user, is_paid=True).first()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'send_otp':
            request.session['resume_data'] = {
                'full_name': request.POST.get('full_name'),
                'email': request.POST.get('email'),
                'phone': request.POST.get('phone'),
                'address': request.POST.get('address'),
                'qualification': request.POST.get('qualification'),
                'experience': request.POST.get('experience'),
                'skills': request.POST.get('skills'),
            }
            otp = generate_otp()
            OTPVerification.objects.filter(user=request.user, purpose='resume').delete()
            OTPVerification.objects.create(user=request.user, otp=otp, purpose='resume')

            send_mail(
                subject='PublicSpace — OTP for Resume Payment',
                message=f'''Hi {request.user.username},

Your OTP for resume payment verification is:

{otp}

This OTP is valid for 10 minutes. Do not share it with anyone.

Team PublicSpace
''',
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[request.user.email],
                fail_silently=True,
            )
            return render(request, 'social/resume_otp.html', {'email': request.user.email})

        elif action == 'verify_otp':
            otp_entered = request.POST.get('otp')
            try:
                otp_obj = OTPVerification.objects.get(
                    user=request.user,
                    purpose='resume',
                    is_verified=False
                )
                if otp_obj.otp == otp_entered:
                    otp_obj.is_verified = True
                    otp_obj.save()
                    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
                    order = client.order.create({
                        'amount': 5000,
                        'currency': 'INR',
                        'payment_capture': 1,
                    })
                    from .models import Payment
                    payment = Payment.objects.create(
                        user=request.user,
                        razorpay_order_id=order['id'],
                        plan='resume',
                        amount=50,
                    )
                    return render(request, 'social/resume_payment.html', {
                        'order': order,
                        'payment': payment,
                        'razorpay_key': settings.RAZORPAY_KEY_ID,
                    })
                else:
                    return render(request, 'social/resume_otp.html', {
                        'email': request.user.email,
                        'error': 'Invalid OTP. Please try again.'
                    })
            except OTPVerification.DoesNotExist:
                return render(request, 'social/resume_otp.html', {
                    'email': request.user.email,
                    'error': 'OTP expired. Please go back and try again.'
                })

    return render(request, 'social/resume_form.html', {
        'existing_resume': existing_resume,
    })

# ── Resume Payment Success ──
@login_required
def resume_payment_success(request):
    if request.method == 'POST':
        razorpay_payment_id = request.POST.get('razorpay_payment_id')
        razorpay_order_id = request.POST.get('razorpay_order_id')

        from .models import Payment, Resume
        try:
            payment = Payment.objects.get(razorpay_order_id=razorpay_order_id)
            payment.razorpay_payment_id = razorpay_payment_id
            payment.paid = True
            payment.save()

            resume_data = request.session.get('resume_data', {})

            buffer = BytesIO()
            doc = SimpleDocTemplate(buffer, pagesize=A4)
            styles = getSampleStyleSheet()
            story = []

            story.append(Paragraph(resume_data.get('full_name', ''), styles['Title']))
            story.append(Spacer(1, 0.2 * inch))

            contact = f"📧 {resume_data.get('email', '')} | 📞 {resume_data.get('phone', '')} | 📍 {resume_data.get('address', '')}"
            story.append(Paragraph(contact, styles['Normal']))
            story.append(Spacer(1, 0.3 * inch))

            sections = [
                ('Qualifications', resume_data.get('qualification', '')),
                ('Experience', resume_data.get('experience', 'No experience listed')),
                ('Skills', resume_data.get('skills', '')),
            ]

            for title, content in sections:
                story.append(Paragraph(f"<b>{title}</b>", styles['Heading2']))
                story.append(Paragraph(content, styles['Normal']))
                story.append(Spacer(1, 0.2 * inch))

            doc.build(story)
            pdf_content = buffer.getvalue()
            buffer.close()

            resume, _ = Resume.objects.get_or_create(user=request.user)
            resume.full_name = resume_data.get('full_name', '')
            resume.email = resume_data.get('email', '')
            resume.phone = resume_data.get('phone', '')
            resume.address = resume_data.get('address', '')
            resume.qualification = resume_data.get('qualification', '')
            resume.experience = resume_data.get('experience', '')
            resume.skills = resume_data.get('skills', '')
            resume.is_paid = True
            resume.resume_file.save(
                f"resume_{request.user.username}.pdf",
                ContentFile(pdf_content)
            )
            resume.save()

            send_mail(
                subject='PublicSpace — Your Resume is Ready!',
                message=f'''Hi {request.user.username},

Your resume has been generated successfully!

Payment ID: {razorpay_payment_id}
Amount Paid: ₹50

Your resume is now attached to your profile and will be used for internship applications.

Team PublicSpace
''',
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[request.user.email],
                fail_silently=True,
            )

            return render(request, 'social/resume_success.html', {'resume': resume})

        except Payment.DoesNotExist:
            return redirect('resume_create')

    return redirect('resume_create')

# ── View Resume ──
@login_required
def view_resume(request):
    from .models import Resume
    resume = Resume.objects.filter(user=request.user, is_paid=True).first()
    return render(request, 'social/view_resume.html', {'resume': resume})

# ── Language Switch with OTP for French ──
def set_language_custom(request):
    if request.method == 'POST':
        language = request.POST.get('language')

        if language == 'fr':
            if not request.user.is_authenticated:
                return redirect('login')
            otp = generate_otp()
            from .models import OTPVerification
            OTPVerification.objects.filter(user=request.user, purpose='language').delete()
            OTPVerification.objects.create(user=request.user, otp=otp, purpose='language')

            send_mail(
                subject='PublicSpace — OTP for Language Change',
                message=f'''Hi {request.user.username},

Your OTP to switch the website language to French is:

{otp}

This OTP is valid for 10 minutes.

Team PublicSpace
''',
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[request.user.email],
                fail_silently=True,
            )
            request.session['pending_language'] = language
            return render(request, 'social/language_otp.html', {'email': request.user.email})
        else:
             translation.activate(language)
             request.session['_language'] = language
             response = redirect(f'/{language}/')
             response.set_cookie(settings.LANGUAGE_COOKIE_NAME, language)
    return response

    return redirect('feed')


def verify_language_otp(request):
    if request.method == 'POST':
        otp_entered = request.POST.get('otp')
        language = request.session.get('pending_language', 'fr')
        from .models import OTPVerification
        try:
            otp_obj = OTPVerification.objects.get(
                user=request.user,
                purpose='language',
                is_verified=False
            )
            if otp_obj.otp == otp_entered:
                otp_obj.is_verified = True
                otp_obj.save()
                translation.activate(language)
                request.session['_language'] = language
                response = redirect('feed')
                response.set_cookie(settings.LANGUAGE_COOKIE_NAME, language)
                return response
            else:
                return render(request, 'social/language_otp.html', {
                    'email': request.user.email,
                    'error': _('Invalid OTP. Please try again.')
                })
        except OTPVerification.DoesNotExist:
            return render(request, 'social/language_otp.html', {
                'email': request.user.email,
                'error': _('OTP expired. Please try again.')
            })
    return redirect('feed')
# ── Chrome Login OTP Verify ──
def verify_chrome_otp(request):
    if request.method == 'POST':
        otp_entered = request.POST.get('otp')
        user_id = request.session.get('pending_login_user')
        ip = request.session.get('pending_login_ip')
        browser = request.session.get('pending_login_browser')
        os_name = request.session.get('pending_login_os')
        device_type = request.session.get('pending_login_device')

        if not user_id:
            return redirect('login')

        try:
            user = User.objects.get(id=user_id)
            from .models import OTPVerification, LoginHistory
            otp_obj = OTPVerification.objects.get(
                user=user,
                purpose='chrome_login',
                is_verified=False
            )
            if otp_obj.otp == otp_entered:
                otp_obj.is_verified = True
                otp_obj.save()

                # Save login history
                LoginHistory.objects.create(
                    user=user,
                    ip_address=ip,
                    browser=browser,
                    os=os_name,
                    device_type=device_type,
                    was_successful=True,
                )

                # Clear session
                del request.session['pending_login_user']
                login(request, user)
                return redirect('feed')
            else:
                return render(request, 'social/chrome_otp.html', {
                    'email': user.email,
                    'error': 'Invalid OTP. Please try again.'
                })
        except (User.DoesNotExist, OTPVerification.DoesNotExist):
            return render(request, 'social/chrome_otp.html', {
                'error': 'OTP expired. Please login again.'
            })
    return redirect('login')

# ── Login History ──
@login_required
def login_history(request):
    from .models import LoginHistory
    history = LoginHistory.objects.filter(user=request.user)
    return render(request, 'social/login_history.html', {'history': history})