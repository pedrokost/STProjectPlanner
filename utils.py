from datetime import timedelta, datetime, date
from operator import attrgetter, methodcaller, itemgetter
import re
from collections import namedtuple

def listdiff(a, b):
    b = set(b)
    return [aa for aa in a if aa not in b]

def sparkline(values, smallest=-1, largest=-1):
	if len(values) == 0:
		return ''

	ticks = ['▁', '▂', '▃', '▄', '▅', '▆', '▇']
	values = [float(val) for val in values]
	smallest = min(values) if smallest == -1 else smallest
	largest = max(values) if largest == -1 else largest
	rng = largest - smallest
	scale = len(ticks) - 1

	if rng == 0:
		rng = largest - 0

	if rng != 0:
		return ''.join([  ticks[min(scale, round(((val - smallest) / rng) * scale))] for val in values  ])
	else:
		return ''.join([ticks[0] for val in values])


def truncate_middle(s, n):
	if len(s) <= n:
		# string is already short-enough
		return s
	# half of the size, minus the 3 .'s
	n_2 = int(int(n) / 2 - 3)
	# whatever's left
	n_1 = int(n - n_2 - 3)

	return '{0}...{1}'.format(s[:n_1], s[-n_2:])


def weeknumber(datetime):
	return datetime.date().isocalendar()[1]

def fmtweek(datetime):
	(year, week) = datetime.isocalendar()[:2]
	return '%04dW%02d' % (year, week)

def next_available_weekday(dt):
	MONDAY=0
	SUNDAY=6

	if dt.weekday() == MONDAY:
		# Skip weekends
		delta = timedelta(days=3)
	elif dt.weekday() == SUNDAY:
		# Skip weekends
		delta = timedelta(days=2)
	else:
		delta = timedelta(days=1)
	return dt - delta

def human_duration(total_duration, duration_categories_map, max_segments=5):
	groupped_duration = dict.fromkeys(duration_categories_map.keys(), 0)

	duration_categories = sorted(duration_categories_map.items(), key=itemgetter(1), reverse=True)
	duration_categories = [d[0] for d in duration_categories]

	for duration_cat in duration_categories:
		# groupped_duration[duration_cat] = int(round(total_duration / duration_categories_map[duration_cat]))
		groupped_duration[duration_cat] = total_duration // duration_categories_map[duration_cat]
		total_duration -= groupped_duration[duration_cat] * duration_categories_map[duration_cat]

	human_time = ' '.join(["%d%s" % (groupped_duration[cat], cat) for cat in duration_categories if groupped_duration[cat] > 0])

	# Cro out low precision (this should be smarte rounding)
	if max_segments < len(human_time.split(' ')):
		human_time = ' '.join(human_time.split(' ')[:max_segments])

	return human_time

def mean(values):
	if len(values) == 0: return None
	return sum(values) / len(values)


def has_optional_flag(string):
	return string is not None and "M" in string

def extract_categories(string):
	CATEGORY_REGEX = '(?P<cat>\w{3,})?\s?(?P<duration>\d+(h|d|w|m|q))?'

	categories = {}

	for match in re.finditer(CATEGORY_REGEX, string):
		if not (match.group('duration') is None and match.group('cat') is None):

			cat = str(match.group('cat'))
			categories[cat] = {}
			if match.group('duration'):
				dur = int(match.group('duration')[:-1])
				unit = match.group('duration')[-1]
			else:
				dur = None
				unit = None

			categories[cat]['duration_value'] = dur,
			categories[cat]['duration_unit'] = unit

	return categories

def parse_end_date(str):
	DATE_FORMAT = '%Y-%m-%d'
	return datetime.strptime(str, DATE_FORMAT) if str else None

def extract_task_metadata(task):
	TASK_META_MATCH_REGEX = '\[((?P<flags>M)(?![a-zA-Z])\s?)?(?P<categories>(\d+\w\s?)?(\w{2,})?(\w{2,}\s\d+\w\s?)*)(?P<end_date>\d{4}-\d{2}-\d{2})?\]$'

	TaskMeta = namedtuple('TaskMeta', ['optional', 'categories', 'end_date'])
	matches = re.search(TASK_META_MATCH_REGEX, task)

	if matches:

		optional = has_optional_flag(matches.group('flags'))
		categories = extract_categories(matches.group('categories'))
		end_date = parse_end_date(matches.group('end_date'))

		meta = TaskMeta(
			optional,
			categories,
			end_date
		)
		raw_meta = matches.group(0)
	else:
		raw_meta = ""
		categories = {}
		categories['None'] = {}
		categories['None']['duration_value'] = None,
		categories['None']['duration_unit'] = None
		meta = TaskMeta(False, categories, None)

	return (meta, raw_meta)


def weighted_sampling_without_replacement(l, n, myrandom=None):
	"""Selects without replacement n random elements from a list of (weight, item) tuples."""

	if myrandom:
		l = sorted((myrandom.random() * x[0], x[1]) for x in l)
	else:
		import random
		l = sorted((random.random() * x[0], x[1]) for x in l)

	return l[-n:]