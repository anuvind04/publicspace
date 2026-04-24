from django.contrib import admin
from .models import FriendRequest, Friendship, Post, Like, Comment

admin.site.register(FriendRequest)
admin.site.register(Friendship)
admin.site.register(Post)
admin.site.register(Like)
admin.site.register(Comment)