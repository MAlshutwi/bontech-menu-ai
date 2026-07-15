# LovableMenuAI

واجهة Lovable جاهزة لعرض قائمة كل مطعم مباشرة من قاعدة البيانات.

## التشغيل محلياً

1. شغّل API من جذر المشروع:

   ```powershell
   .\ToCoun\run-demo.ps1
   ```

2. انسخ `.env.example` إلى `.env` داخل هذا المجلد واترك `VITE_MENU_API_URL=http://127.0.0.1:8000`.
3. شغّل `npm install` ثم `npm run dev`.

## في Lovable

ارفع هذا المجلد إلى مستودع GitHub ثم استورده إلى Lovable، أو انسخ محتوى `lovable_prompt.md` إلى مشروع React جديد وانسخ مجلد `src`.

انشر API أولاً عبر Render حسب [دليل Render](../RENDER_DEPLOY.md)، ثم في إعدادات Lovable أضف متغير البيئة التالي فقط:

```
VITE_MENU_API_URL=https://YOUR-BONTECH-API
```

قاعدة البيانات تبقى خلف API؛ لا تضع ملف `ToCoun/.env` أو بيانات الاتصال في Lovable أو في GitHub.
