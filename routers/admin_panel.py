from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
import os
import json
from datetime import time

from scripts.db import get_db
from scripts.crud import create_user, get_user_by_login
from routers.dependencies import get_current_user, RoleGuard

router = APIRouter()

class AdminRegisterData(BaseModel):
    login: str
    password: str
    role: str = "developer"  # По умолчанию developer
    full_name: str | None = None

class WorktimeSettings(BaseModel):
    holidays: str = ""  # Даты праздников в формате "YYYY-MM-DD,YYYY-MM-DD"
    schedule: dict = None  # График работы по дням недели

# Путь к конфигурационному файлу
CONFIG_FILE = "worktime_config.json"

def load_worktime_config():
    """Загружает настройки рабочего времени из файла"""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        # Значения по умолчанию
        return {
            "holidays": "",
            "schedule": {
                "monday": {"start": "07:45", "end": "17:00"},
                "tuesday": {"start": "07:45", "end": "17:00"},
                "wednesday": {"start": "07:45", "end": "17:00"},
                "thursday": {"start": "07:45", "end": "17:00"},
                "friday": {"start": "07:45", "end": "15:45"},
                "saturday": None,  # выходной
                "sunday": None     # выходной
            }
        }

def save_worktime_config(config):
    """Сохраняет настройки рабочего времени в файл"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

@router.post("/admin/reg", dependencies=[Depends(RoleGuard("admin"))])
def admin_register(data: AdminRegisterData, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    """
    Админский эндпоинт для регистрации пользователей с возможностью указания роли
    Доступен только для администраторов
    """
    admin_user = get_user_by_login(db, current_user)
    if not admin_user or admin_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can register new users")
    
    # Проверяем, что указанная роль допустима
    allowed_roles = ["developer", "norm_controller", "admin"]
    if data.role not in allowed_roles:
        raise HTTPException(status_code=400, detail=f"Role must be one of: {', '.join(allowed_roles)}")
    
    # Создаем пользователя с указанной ролью
    user = create_user(db, data.login, data.password, full_name=data.full_name, role=data.role)
    if not user:
        raise HTTPException(status_code=400, detail="Registration failed")
    
    return {
        "ok": True,
        "user_id": user.id,
        "login": user.login,
        "role": user.role,
        "full_name": user.full_name
    }

@router.get("/admin/worktime-settings", dependencies=[Depends(RoleGuard("admin"))])
def get_worktime_settings(current_user: str = Depends(get_current_user)):
    """
    Получить текущие настройки рабочего времени
    Доступно только для администраторов
    """
    config = load_worktime_config()
    
    return {
        "holidays": config["holidays"],
        "schedule": config["schedule"]
    }

@router.get("/admin/users", dependencies=[Depends(RoleGuard("admin"))])
def get_all_users(db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    """
    Получить список всех пользователей (логин, ФИО), кроме самого администратора
    Доступно только для администраторов
    """
    admin_user = get_user_by_login(db, current_user)
    if not admin_user or admin_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can access user list")
    
    # Получаем всех пользователей, кроме текущего администратора
    from scripts.models import User
    all_users = db.query(User).all()
    
    users_list = []
    for user in all_users:
        # Исключаем самого администратора из списка
        if user.login != current_user:
            users_list.append({
                "id": user.id,
                "login": user.login,
                "full_name": user.full_name,
                "role": user.role
            })
    
    return {
        "users": users_list,
        "total_count": len(users_list)
    }


@router.post("/admin/worktime-settings", dependencies=[Depends(RoleGuard("admin"))])
def update_worktime_settings(settings: WorktimeSettings, current_user: str = Depends(get_current_user)):
    """
    Обновить настройки рабочего времени
    Доступно только для администраторов
    """
    config = load_worktime_config()
    
    if settings.holidays is not None:
        config["holidays"] = settings.holidays
    
    if settings.schedule is not None:
        # Проверяем структуру расписания
        valid_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        for day, day_schedule in settings.schedule.items():
            if day not in valid_days:
                raise HTTPException(status_code=400, detail=f"Invalid day: {day}")
            if day_schedule is not None:
                if "start" not in day_schedule or "end" not in day_schedule:
                    raise HTTPException(status_code=400, detail=f"Invalid schedule format for {day}")
        
        config["schedule"] = settings.schedule
    
    save_worktime_config(config)
    
    return {
        "ok": True,
        "holidays": config["holidays"],
        "schedule": config["schedule"],
        "message": "Worktime settings updated successfully"
    }