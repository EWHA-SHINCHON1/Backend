# 한국어(ko) 로케일 날짜·숫자 형식
# Django 기본 ko 형식을 덮어씁니다.
# https://docs.djangoproject.com/en/5.2/topics/i18n/formatting/#creating-custom-format-files

DATE_FORMAT = 'Y-m-d'
TIME_FORMAT = 'H:i'
DATETIME_FORMAT = 'Y-m-d H:i'
SHORT_DATE_FORMAT = 'Y-m-d'
SHORT_DATETIME_FORMAT = 'Y-m-d H:i'

DATE_INPUT_FORMATS = [
    '%Y-%m-%d',  # '2026-09-26'
    '%Y.%m.%d',  # '2026.09.26'
]
DATETIME_INPUT_FORMATS = [
    '%Y-%m-%d %H:%M:%S',  # '2026-09-26 14:30:59'
    '%Y-%m-%d %H:%M',  # '2026-09-26 14:30'
    '%Y-%m-%d',  # '2026-09-26'
]

DECIMAL_SEPARATOR = '.'
THOUSAND_SEPARATOR = ','
NUMBER_GROUPING = 3
