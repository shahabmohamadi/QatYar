"""Normalize common Persian spelling and product-code variations for lookup."""
import unicodedata

DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
LETTERS = str.maketrans({'ي': 'ی', 'ى': 'ی', 'ك': 'ک', 'آ': 'ا', 'أ': 'ا', 'إ': 'ا', 'ٱ': 'ا', 'ة': 'ه', 'ۀ': 'ه'})


def normalize_name(value):
    value = unicodedata.normalize('NFKC', str(value or '')).translate(DIGITS).translate(LETTERS)
    value = value.replace('\u200c', ' ').replace('\u0640', '')
    value = ''.join(char for char in value if unicodedata.category(char) != 'Mn')
    return ' '.join(value.casefold().split())


def normalize_code(value):
    value = normalize_name(value)
    return ''.join(char for char in value if char.isalnum())
