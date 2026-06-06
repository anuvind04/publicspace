from django.urls import path
from . import views

urlpatterns = [
    path('', views.feed, name='feed'),
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
path('forgot-password/', views.forgot_password, name='forgot_password'),
path('resume/create/', views.resume_create, name='resume_create'),
path('resume/payment/success/', views.resume_payment_success, name='resume_payment_success'),
path('resume/view/', views.view_resume, name='view_resume'),
    path('post/create/', views.create_post, name='create_post'),
    path('post/like/<int:pk>/', views.like_post, name='like_post'),
    path('post/comment/<int:pk>/', views.add_comment, name='add_comment'),
    path('post/delete/<int:pk>/', views.delete_post, name='delete_post'),
    path('people/', views.people, name='people'),
    path('people/add/<int:pk>/', views.send_request, name='send_request'),
    path('friends/accept/<int:pk>/', views.accept_request, name='accept_request'),
    path('notifications/', views.notifications, name='notifications'),
    path('forgot-password/', views.forgot_password, name='forgot_password'),
    path('plans/', views.plans, name='plans'),
path('plans/order/<str:plan>/', views.create_order, name='create_order'),
path('payment/success/', views.payment_success, name='payment_success'),
path('internship/apply/', views.apply_internship, name='apply_internship'),
path('internship/my-applications/', views.my_applications, name='my_applications'),
]