# Загрузка на GitHub

Перед загрузкой переключите репозиторий bokolyar2la/AI-Sales-Copilot в Private, если хотите сохранить код закрытым.

## Из текущей папки проекта в VS Code

Откройте Terminal → New Terminal. Выполните:

```powershell
git add -A
git status --short
git commit -m "Add sales dialogue planning and evaluation report"
git push -u origin HEAD:main
```

origin уже настроен на https://github.com/bokolyar2la/AI-Sales-Copilot.git. При первом push GitHub может предложить вход через браузер. API-ключ DeepSeek не является паролем GitHub.

Если Git попросит имя и email автора, задайте свои значения через git config user.name и git config user.email, затем повторите commit. Если push отклонён из-за существующих изменений на GitHub, не используйте --force: сначала разберите расхождение истории.

## Из архива в новой папке

Распакуйте архив и откройте папку ai-sales-copilot в VS Code. В терминале этой папки:

```powershell
git init -b main
git add -A
git commit -m "Initial AI Sales Copilot Alpha"
git remote add origin https://github.com/bokolyar2la/AI-Sales-Copilot.git
git push -u origin main
```

Этот вариант подходит для пустого удалённого репозитория. В архиве нет .git и локальных секретов. Не загружайте вручную .env, data, .venv, node_modules или личные записи разговоров.
