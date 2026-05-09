from organizer_extraction_app import models as organizer_models
from django.contrib import messages
from django.shortcuts import redirect


def get_or_create_profile(user):
    """Helper function to get or create user profile"""
    try:
        return user.profile
    except organizer_models.UserProfile.DoesNotExist:
        profile = organizer_models.UserProfile.objects.create(
            user=user,
            role='admin' if user.is_superuser else 'admin',
            is_active=user.is_active
        )
        return profile


def role_required(roles):
    """Decorator to check if user has required role"""
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                messages.error(request, "Please login to access this page.")
                return redirect('login')
            
            user_profile = get_or_create_profile(request.user)
            
            if user_profile.role not in roles:
                messages.error(request, "You don't have permission to access this page.")
                return redirect('document_list')
            
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator