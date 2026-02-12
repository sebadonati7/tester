# 🚀 SIRAYA Health Navigator - Quick Start Guide

## After These Fixes

This guide helps you get started with SIRAYA after the recent fixes for HTML rendering, Supabase logging, and dashboard access.

---

## ✅ What Was Fixed

1. **HTML Rendering** - Graphics now display properly (no more raw HTML code)
2. **Supabase Logging** - Enhanced error handling and diagnostics
3. **Dashboard Password** - Default password documented and clearly shown

---

## 🔧 Setup Instructions

### 1. Configure API Keys

Copy the example secrets file:
```bash
cp .streamlit/secrets.toml.EXAMPLE .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml` and add your API keys:

```toml
[groq]
api_key = "gsk_YOUR_GROQ_KEY"

[gemini]
api_key = "AIzaYOUR_GEMINI_KEY"

SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_KEY = "eyJYOUR_SERVICE_ROLE_KEY"
```

**Where to get keys:**
- **Groq**: https://console.groq.com/keys
- **Gemini**: https://aistudio.google.com/app/apikey
- **Supabase**: Dashboard → Settings → API → `service_role` key (NOT anon!)

### 2. Configure Supabase (if using logging)

If you want conversation logging:

1. Go to Supabase Dashboard → SQL Editor
2. Open `.streamlit/fix_rls.sql`
3. Copy and paste the entire script
4. Execute it

This creates the necessary Row Level Security policies.

### 3. Start the Application

```bash
streamlit run siraya/app.py
```

---

## 🧪 Verify Everything Works

### Check #1: HTML Rendering
✅ Open the app  
✅ Look at the sidebar - logo should be nicely formatted  
✅ Start a conversation - step tracker should show colored circles  
✅ Go to Map view - facility cards should have proper styling  

❌ If you see HTML code like `<div>`, something is wrong

### Check #2: Supabase Logging
✅ Open the sidebar  
✅ Look for "📡 Stato Sistema"  
✅ Should show "✅ Database Connesso" (if configured)  
✅ Have a conversation  
✅ Check Supabase Dashboard → Table Editor → triage_logs  
✅ New records should appear  

ℹ️ If not configured, it will show "⚠️ Database Non Configurato" - this is OK if you don't need logging

### Check #3: Dashboard Access
✅ Click "📊 Analytics Dashboard" in sidebar  
✅ Should show info box with password  
✅ Enter password: `ciaociao`  
✅ Should grant access  

💡 To change password, add `BACKEND_PASSWORD = "yourpassword"` to secrets.toml

---

## 🐛 Troubleshooting

### Problem: HTML still showing as code
**Solution:**
1. Clear browser cache (Ctrl+Shift+Delete)
2. Restart Streamlit server (Ctrl+C, then restart)
3. Check Streamlit version: `streamlit --version` (should be >= 1.30)

### Problem: "⚠️ Database Offline" or logs not writing
**Solution:**
1. Verify you're using SERVICE_ROLE key (not anon key)
2. Run the `.streamlit/fix_rls.sql` script in Supabase
3. Click debug expander in sidebar for more info
4. Check application logs for detailed errors

### Problem: Cannot access dashboard
**Solution:**
1. Try default password: `ciaociao`
2. Check if you set custom password in secrets.toml
3. Restart app after changing secrets
4. Check terminal logs for authentication errors

### Problem: CSS styles not loading
**Solution:**
1. Some CSS files use `unsafe_allow_html` which is OK for styles
2. This doesn't affect functionality, only minor styling
3. Clear cache and reload if styles look wrong

---

## 📝 Configuration Reference

### Minimal Configuration (AI only)
```toml
[groq]
api_key = "gsk_..."

[gemini]
api_key = "AIza..."
```

### Full Configuration (AI + Logging + Custom Password)
```toml
[groq]
api_key = "gsk_..."

[gemini]
api_key = "AIza..."

SUPABASE_URL = "https://..."
SUPABASE_KEY = "eyJ..."

BACKEND_PASSWORD = "mysecurepassword"
```

---

## 🎯 Key Changes Made

### HTML Rendering
- **Before**: Used `st.markdown(html, unsafe_allow_html=True)` - sometimes failed
- **After**: Uses `st.components.v1.html()` - always works
- **Impact**: All graphics display reliably

### Supabase Logging
- **Before**: Silent failures, no diagnostic info
- **After**: Detailed error logging, debug expanders, RLS scripts
- **Impact**: Easy to troubleshoot logging issues

### Dashboard Access
- **Before**: No documentation, users confused about password
- **After**: Info box on login, comprehensive documentation
- **Impact**: Clear access instructions

---

## 📚 Additional Documentation

- **Complete Details**: See `FIXES_SUMMARY.md`
- **Configuration Template**: See `.streamlit/secrets.toml.EXAMPLE`
- **SQL Scripts**: See `.streamlit/fix_rls.sql`

---

## 🔒 Security Reminders

⚠️ **Never commit `.streamlit/secrets.toml`** - it's already in `.gitignore`  
⚠️ **Use SERVICE_ROLE key** for Supabase (required for logging)  
⚠️ **Change default password** in production (`BACKEND_PASSWORD` in secrets)  
⚠️ **Keep API keys secret** - don't share or expose them  

---

## 🎉 You're Ready!

Everything should now work properly:
- ✅ Graphics display correctly
- ✅ Logging works (if configured)
- ✅ Dashboard access is clear
- ✅ Configuration is documented

Start using SIRAYA Health Navigator! 🩺

---

**Need Help?** Check:
1. Terminal logs for errors
2. Sidebar debug expanders
3. `FIXES_SUMMARY.md` for troubleshooting
4. Supabase logs (Dashboard → Logs → Database)

**Last Updated:** 2026-02-12  
**Version:** SIRAYA v2.0 (Fixed)
