"""Literal quantity equivalence in digits, English and Chinese prose, without rounding or unit conversion.

Exact numeric values, percentages and explicitly vague ranges are distinct.
The visual encodings still require verbatim per-observation evidence separately.
"""
from decimal import Decimal, InvalidOperation
import re

ARABIC_NUMBERS = re.compile(r"(?<![A-Za-z0-9_])[-+−]?\d+(?:[.,]\d+)*(?:%|％)?(?![A-Za-z0-9_])")
CHINESE_NUMBERS = re.compile(
    r"(?:[零〇一二兩两三四五六七八九十百千萬万億亿兆]{2,}(?:[點点][零〇一二兩两三四五六七八九]+)?"
    r"|[零〇一二兩两三四五六七八九][點点][零〇一二兩两三四五六七八九]+"
    r"|[十百千萬万億亿兆]"
    r"|[零〇一二兩两三四五六七八九](?=個|人|台|次|年|月|日|度|克|元|吋|寸|尼特|赫茲|分鐘|秒))"
)
_DIGITS = dict(zip('零〇一二兩两三四五六七八九', [0,0,1,2,2,2,3,4,5,6,7,8,9]))
_UNITS = {'十':10,'百':100,'千':1000,'萬':10000,'万':10000,'億':100000000,'亿':100000000,'兆':1000000000000}
_WORDS = {'zero':0,'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,
          'ten':10,'eleven':11,'twelve':12,'thirteen':13,'fourteen':14,'fifteen':15,'sixteen':16,
          'seventeen':17,'eighteen':18,'nineteen':19,'twenty':20,'thirty':30,'forty':40,'fifty':50,
          'sixty':60,'seventy':70,'eighty':80,'ninety':90}
_SCALES = {'hundred':100,'thousand':1000,'million':1000000,'billion':1000000000,'trillion':1000000000000}
_WORD = '(?:' + '|'.join([*_WORDS, *_SCALES]) + ')'
_SPOKEN = re.compile(r'(?<![\w])(?:minus\s+)?' + _WORD + r'(?:[ -]+(?:and[ -]+)?' + _WORD + r')*(?:\s+point(?:[ -]+(?:' + '|'.join(list(_WORDS)[:10]) + r'))+)?(?![\w])', re.I)
_SCALED_DIGITS = re.compile(r'(?<![A-Za-z0-9_])([-+−]?\d+(?:[.,]\d+)*)\s*(trillion\b|billion\b|million\b|thousand\b|hundred\b|[十百千萬万億亿兆]+)', re.I)
_VAGUE = [
    (r'\bhundreds of billions\b|數以千億計|数以千亿计|數千億|数千亿', 'hundreds-of-billions'),
    (r'\btens of billions\b|數以百億計|数以百亿计|數百億|数百亿', 'tens-of-billions'),
    (r'\bbillions of\b|數以十億計|数以十亿计|數十億|数十亿', 'billions'),
    (r'\bhundreds of millions\b|數以億計|数以亿计|數億|数亿', 'hundreds-of-millions'),
    (r'\bhundreds of thousands\b|數以十萬計|数以十万计|數十萬|数十万', 'hundreds-of-thousands'),
    (r'\btens of millions\b|數以千萬計|数以千万计|數千萬|数千万', 'tens-of-millions'),
    (r'\btens of thousands\b|數以萬計|数以万计|數萬|数万', 'tens-of-thousands'),
    (r'\bmillions of\b|數以百萬計|数以百万计|數百萬|数百万', 'millions'),
    (r'\bthousands of\b|數以千計|数以千计|數千|数千', 'thousands'),
    (r'\bhundreds of\b|數以百計|数以百计|數百|数百', 'hundreds'),
]
_CN_PERCENT = re.compile(r'百分之([-+−]?\d+(?:[.,]\d+)*|[零〇一二兩两三四五六七八九十百千萬万億亿兆點点]+)')


def _decimal(token):
    token = token.replace('−','-')
    if ',' in token:
        if not re.fullmatch(r'[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?', token):
            return token  # Ambiguous locale formatting stays literal.
        token = token.replace(',','')
    try:
        return Decimal(token)
    except InvalidOperation:
        return token


def _chinese(token):
    integer, _, fraction = re.sub('点','點',token).partition('點')
    if not any(char in _UNITS for char in integer):
        result = Decimal(''.join(str(_DIGITS[char]) for char in integer))
    else:
        total = section = digit = 0
        for char in integer:
            if char in _DIGITS:
                digit = _DIGITS[char]
            elif _UNITS[char] < 10000:
                section += (digit or 1) * _UNITS[char]
                digit = 0
            else:
                unit = _UNITS[char]
                group = section + digit
                if unit >= 100000000:
                    total = (total + group or 1) * unit
                else:
                    total += (group or 1) * unit
                section = digit = 0
        result = Decimal(total + section + digit)
    if fraction:
        result += Decimal('0.' + ''.join(str(_DIGITS[char]) for char in fraction))
    return result


def _english(token):
    token = token.lower().replace('-',' ')
    negative = token.startswith('minus ')
    token = token.removeprefix('minus ')
    integer, _, fraction = token.partition(' point ')
    total = group = 0
    last_number = None
    hundred_used = has_group = False
    last_scale = float('inf')
    for word in integer.split():
        if word == 'and':
            continue
        if word in _WORDS:
            number = _WORDS[word]
            if last_number is not None:
                if not (20 <= last_number <= 90 and last_number % 10 == 0 and 1 <= number <= 9):
                    raise ValueError('Ambiguous spoken number sequence')
                last_number += number
            else:
                last_number = number
            group += number
            has_group = True
        elif word == 'hundred':
            if hundred_used or (has_group and not 0 <= group <= 9):
                raise ValueError('Ambiguous spoken number scale')
            group = (group if has_group else 1) * 100
            hundred_used = has_group = True
            last_number = None
        else:
            scale = _SCALES[word]
            if scale >= last_scale:
                raise ValueError('Ambiguous spoken number scales')
            total += (group if has_group else 1) * scale
            group = 0
            has_group = hundred_used = False
            last_number = None
            last_scale = scale
    value = Decimal(total + group)
    if fraction:
        value += Decimal('0.' + ''.join(str(_WORDS[word]) for word in fraction.split()))
    return -value if negative else value


def _kind(text, end, percent=False):
    return 'percent' if percent or re.match(r'\s*(?:%|％|percent\b|per\s+cent\b)', text[end:], re.I) else 'number'


def number_tokens(text, *, source=False):
    """Return Arabic, Chinese and spoken tokens in equivalent comparable forms."""
    arabic, chinese, spoken = set(), set(), set()
    masked = text
    for pattern, category in _VAGUE:
        def vague(match):
            (chinese if re.search(r'[數数]', match.group()) else spoken).add(('range',category))
            return ' ' * len(match.group())
        masked = re.sub(pattern,vague,masked,flags=re.I)
    def percent(match):
        value=match.group(1)
        chinese.add(('percent',_decimal(value) if re.match(r'[-+−]?\d',value) else _chinese(value)))
        return ' ' * len(match.group())
    masked = _CN_PERCENT.sub(percent,masked)
    def scaled(match):
        coefficient = _decimal(match.group(1))
        if not isinstance(coefficient, Decimal):
            return match.group()  # Do not guess ambiguous comma formatting.
        unit = match.group(2)
        multiplier = _SCALES[unit.lower()] if unit.lower() in _SCALES else _chinese(unit)
        arabic.add((_kind(masked, match.end()), coefficient * multiplier))
        return ' ' * len(match.group())
    masked = _SCALED_DIGITS.sub(scaled, masked)
    # Temporal adverb, not a temperature/measurement; retain quantitative uses such as「一度低溫」.
    masked = re.sub(r'一度(?=懸|悬|陷|成為|成为|被|讓|让|引|出現|出现)', '  ', masked)
    # 每一個 / 每一項 are distributive grammar, not a measured count of one.
    masked = re.sub(r'(?<=每)一(?=[個个位項项份種种件類类])', ' ', masked)
    for match in ARABIC_NUMBERS.finditer(masked):
        raw=match.group();is_percent=raw.endswith(('%','％'))
        arabic.add((_kind(masked,match.end(),is_percent),_decimal(raw.rstrip('%％'))))
    for match in CHINESE_NUMBERS.finditer(masked):
        value=_chinese(match.group())
        if match.start() and masked[match.start()-1] in {'負','负','−','-'}:
            value=-value
        kind = _kind(masked,match.end())
        tail = masked[match.end():]
        prefix = masked[max(0,match.start()-5):match.start()]
        # A non-quantified 一個 is an indefinite noun phrase, not a measurement of one.
        quantified = re.search(r'只有|僅有|仅有|只剩|至少|最多|恰好|剛好|刚好|合計|合计|總共|总共|唯一|第', prefix)
        duration = re.match(r'[個个](?:月|年|星期|禮拜|礼拜|小時|小时)', tail)
        if value == 1 and tail.startswith(('個','个','項','项','位','份')) and not quantified and not duration:
            kind = 'indefinite'
        chinese.add((kind,value))
    for match in _SPOKEN.finditer(masked):
        phrase = match.group()
        if phrase.lower() in _SCALES and re.search(r'\b(?:half|quarter)(?:\s+a)?\s*$', masked[:match.start()], re.I):
            continue
        try:
            spoken.add((_kind(masked,match.end()),_english(phrase)))
        except ValueError:
            # Stuttered/listed numbers are not permission to add them together.
            for word in re.findall(r'\b(?:' + '|'.join(_WORDS) + r')\b', phrase, re.I):
                spoken.add((_kind(masked,match.end()),Decimal(_WORDS[word.lower()])))
    # An English indefinite article is the literal counterpart of a Chinese indefinite 一個.
    # It contributes evidence for one, without making English prose itself assert an extra metric.
    if source and re.search(r'\b(?:a|an)\b', masked, re.I):
        spoken.add(('indefinite',Decimal(1)))
    # English singular duration phrases explicitly refer to one period, commonly rendered「過去一年」.
    if re.search(r'\b(?:the\s+)?(?:past|last|previous)\s+(?:year|month|week|day)\b|\b(?:a|an)\s+(?:year|month|week|day|hour|minute|second)\b', masked,re.I):
        spoken.add(('number',Decimal(1)))
    return arabic,chinese,spoken
