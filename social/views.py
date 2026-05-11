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
        try:
            user = User.objects.get(email=identifier)
        except User.DoesNotExist:
            try:
                from .models import UserProfile
                profile = UserProfile.objects.get(phone=identifier)
                user = profile.user
            except UserProfile.DoesNotExist:
                error = "No account found with that email or phone number."

        if user:
            from .models import UserProfile
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