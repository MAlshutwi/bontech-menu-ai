# نشر BonTech على Render المجاني

هذا المشروع جاهز للنشر كـ **Render Web Service**. يشمل صورة Docker ملف النموذج من:

`ToCoun/Final/bontech_recommendation_model_v1_1_0.joblib`

ولا يرفع أو يقرأ ملف `ToCoun/.env` في GitHub.

## الخطوات

1. ارفع مجلد المشروع بالكامل إلى مستودع GitHub خاص. تأكد أن `ToCoun/Final/bontech_recommendation_model_v1_1_0.joblib` موجود في المستودع، ولا ترفع `ToCoun/.env`.
2. في [Render Dashboard](https://dashboard.render.com/) اختر **New → Blueprint** واربط مستودع GitHub.
3. سيكتشف Render ملف `render.yaml` وينشئ خدمة `bontech-menu-ai` بالخطة المجانية.
4. في صفحة Environment للخدمة أدخل القيم التالية كـ **Secrets**:

   - `DB_HOST`
   - `DB_NAME`
   - `DB_USER`
   - `DB_PASS`
   - `API_KEY` (اختياري في الديمو، مطلوب عند تفعيل حماية API)

   `DB_PORT` مضبوط مسبقاً على `5432`.

5. انشر الخدمة. بعد اكتمال النشر افتح:

   - `https://YOUR-SERVICE.onrender.com/health`
   - `https://YOUR-SERVICE.onrender.com/api/menu/restaurants`

6. في Lovable أضف متغير البيئة:

   ```text
   VITE_MENU_API_URL=https://YOUR-SERVICE.onrender.com
   ```

## تنبيه اتصال قاعدة البيانات

يجب أن تكون قاعدة البيانات قابلة للوصول من Render عبر الإنترنت وSSL/شبكة مسموحة، أو عبر بوابة API/شبكة خاصة بينهما. لا تضع أي كلمة مرور في Lovable أو GitHub.

## حد الخطة المجانية

خدمة Render المجانية قد تتوقف عند الخمول، وتعود تلقائياً مع أول طلب. لذلك يكون أول فتح للموقع بعد التوقف أبطأ من المعتاد.
