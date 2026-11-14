"""Interactive local setup; never print or log credentials."""
import asyncio
import getpass
import os
from pathlib import Path
import httpx
from dotenv import dotenv_values, set_key
from .providers import CompatibleProvider
from .context import apply_analysis

ROOT = Path(__file__).resolve().parents[1]

def error_message(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        return {401: 'Ключ не принят. Проверьте, что скопировали его полностью.',
                402: 'Недостаточно средств на балансе API.',
                403: 'Провайдер отклонил доступ. Проверьте доступность сервиса для аккаунта.',
                404: 'Модель или адрес API не найдены.',
                429: 'Превышен лимит запросов. Попробуйте позже.'}.get(exc.response.status_code, 'Ошибка провайдера. Проверьте настройки и повторите позже.')
    if isinstance(exc, httpx.TimeoutException):
        return 'Модель не ответила за отведённое время. Попробуйте позже.'
    if isinstance(exc, httpx.RequestError):
        return 'Не удалось соединиться с провайдером. Проверьте интернет.'
    return 'Ответ модели не прошёл проверку структуры или источников. Настройки не сохранены.'

async def probe():
    session = {'id': 'setup-test', 'turns': [{'id':'setup-turn', 'role':'client', 'text':'Я бухгалтер. Хочу научиться делать таблицы для расчёта стоимости.'}], 'facts':[], 'recommendations':[], 'gaps':[]}
    analysis, usage = await CompatibleProvider().analyze(session, [])
    apply_analysis(session, analysis)
    return usage

def main():
    print('Подключение модели к AI Sales Copilot\n')
    print('1 — DeepSeek Flash напрямую\n2 — Другой совместимый провайдер')
    choice = input('Выберите вариант [1]: ').strip() or '1'
    if choice == '1':
        endpoint, model = 'https://api.deepseek.com', 'deepseek-flash'
    elif choice == '2':
        endpoint = input('HTTPS-адрес API: ').strip().rstrip('/')
        model = input('Идентификатор модели: ').strip()
        if not endpoint.startswith('https://') or not model:
            print('Нужны HTTPS-адрес и имя модели. Изменений нет.')
            return
    else:
        print('Неизвестный вариант. Изменений нет.')
        return
    print('\nКлюч вводится скрыто. Вставьте его и нажмите Enter. Не отправляйте ключ в чат.')
    key = getpass.getpass('API-ключ: ').strip()
    if not key or any(c.isspace() for c in key):
        print('Пустой ключ или пробелы в ключе. Изменений нет.')
        return
    print(f'\nПроверка: {endpoint}, модель {model}.')
    print('Будет отправлена одна вымышленная реплика бухгалтера; возможна небольшая плата по тарифу API.')
    if input('Выполнить проверку и сохранить подключение? [да/нет]: ').strip().lower() not in ('да','yes'):
        print('Отменено. Изменений нет.')
        return
    config = {'LLM_PROVIDER':'compatible','LLM_BASE_URL':endpoint,'LLM_MODEL':model,'LLM_API_KEY':key,'LLM_TIMEOUT':'45','LLM_MAX_TOKENS':'3000'}
    os.environ.update(config)
    try:
        asyncio.run(probe())
    except Exception as exc:
        print('\n' + error_message(exc))
        return
    env = ROOT / '.env'
    existing = dotenv_values(env) if env.exists() else {}
    # Preserve unrelated settings and write credentials only to ignored .env.
    for name, value in config.items():
        set_key(str(env), name, value, encoding='utf-8')
    if 'DATABASE_PATH' not in existing:
        set_key(str(env), 'DATABASE_PATH', 'data/copilot.sqlite3', encoding='utf-8')
    print('\nГотово: модель ответила, структура и источники проверены. Настройки сохранены локально.')
    print('Остановите старое окно запуска Ctrl+C, снова запустите Start.cmd и обновите страницу.')
    print('Новые реплики будут передаваться выбранному провайдеру. Старые демо-факты лучше не использовать: создайте новый разговор.')

if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('\nОтменено.')
