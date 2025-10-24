from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
from scripts.db import get_db
from scripts.crud import get_user_by_login

def get_current_user(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    return user

class RoleGuard:
    """
    Простой guard по ролям. Использование:
      @router.post("/...", dependencies=[Depends(RoleGuard("norm_controller","admin"))])
    Если список ролей пустой — пропускает любого авторизованного пользователя.
    """
    def __init__(self, *roles: str):
        self.roles = set(roles)

    def __call__(self, request: Request, db: Session = Depends(get_db)):
        user_login = getattr(request.state, "user", None)
        if not user_login:
            raise HTTPException(status_code=401, detail="Could not validate credentials")

        user = get_user_by_login(db, user_login)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        if self.roles and getattr(user, "role", "developer") not in self.roles:
            raise HTTPException(status_code=403, detail="Forbidden")

        # можно вернуть сам логин — он уже есть в request.state.user
        return user_login
