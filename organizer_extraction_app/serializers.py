from rest_framework import serializers
from django.contrib.auth.models import User
from organizer_extraction_app import models as organizer_models
from organizer_extraction_app import constants as organizer_constants


class UserSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(choices=organizer_constants.ROLE_CHOICES)
    is_active = serializers.BooleanField(required=False, default=True)
    password = serializers.CharField(write_only=True, required=False, min_length=8)
    confirm_password = serializers.CharField(write_only=True, required=False)
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 
                  'password', 'confirm_password', 'role', 'is_active']
        extra_kwargs = {
            'email': {'required': True},
            'first_name': {'required': True},
            'last_name': {'required': True},
        }
    
    def validate(self, data):
        # Check if passwords match for creation
        if 'password' in data or 'confirm_password' in data:
            if data.get('password') != data.get('confirm_password'):
                raise serializers.ValidationError({"password": "Passwords don't match"})
        
        # Check username uniqueness for creation
        if not self.instance:  # Creating new user
            if User.objects.filter(username=data.get('username')).exists():
                raise serializers.ValidationError({"username": "Username already exists"})
        else:  # Updating existing user
            if User.objects.filter(username=data.get('username')).exclude(id=self.instance.id).exists():
                raise serializers.ValidationError({"username": "Username already exists"})
        
        # Check email uniqueness
        if not self.instance:
            if User.objects.filter(email=data.get('email')).exists():
                raise serializers.ValidationError({"email": "Email already exists"})
        else:
            if User.objects.filter(email=data.get('email')).exclude(id=self.instance.id).exists():
                raise serializers.ValidationError({"email": "Email already exists"})
        
        return data
    
    def create(self, validated_data):
        role = validated_data.pop('role')
        is_active = validated_data.pop('is_active', True)
        password = validated_data.pop('password')
        validated_data.pop('confirm_password', None)
        
        # Create user
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            password=password,
            is_active=is_active
        )
        
        # Create profile (CRUD approach)
        organizer_models.UserProfile.objects.create(
            user=user,
            role=role,
            is_active=is_active
        )
        
        return user
    
    def update(self, instance, validated_data):
        role = validated_data.pop('role', None)
        is_active = validated_data.pop('is_active', None)
        validated_data.pop('password', None)
        validated_data.pop('confirm_password', None)
        
        # Update user fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Update or create profile
        try:
            profile = instance.profile
            if role:
                profile.role = role
            if is_active is not None:
                profile.is_active = is_active
            profile.save()
        except organizer_models.UserProfile.DoesNotExist:
            # Create profile if it doesn't exist
            organizer_models.UserProfile.objects.create(
                user=instance,
                role=role or 'admin',
                is_active=is_active if is_active is not None else True
            )
        
        return instance