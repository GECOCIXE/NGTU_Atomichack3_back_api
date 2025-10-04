from fastapi import Request, HTTPException

def get_current_user(request: Request):
    user = request.state.user
    if not user:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    return user