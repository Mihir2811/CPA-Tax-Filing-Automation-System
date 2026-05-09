class TrackingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Skip CSRF check for tracking
        if '/tax/track-email/' in request.path:
            setattr(request, '_dont_enforce_csrf_checks', True)
        
        return self.get_response(request)
 