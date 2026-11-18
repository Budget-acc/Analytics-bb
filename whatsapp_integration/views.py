from django.shortcuts import redirect

def redirect_to_whatsapp(request, number):
    # optional: sanitize the number if needed
    whatsapp_url = f"https://wa.me/{number}"
    return redirect(whatsapp_url)
