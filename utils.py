from datetime import timedelta, datetime, date
from operator import attrgetter, methodcaller, itemgetter

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
		groupped_duration[duration_cat] = int(round(total_duration / duration_categories_map[duration_cat]))
		total_duration -= groupped_duration[duration_cat] * duration_categories_map[duration_cat]

	human_time = ' '.join(["%d%s" % (groupped_duration[cat], cat) for cat in duration_categories if groupped_duration[cat] > 0])


	# Cro out low precision (this should be smarte rounding)
	if max_segments < len(human_time.split(' ')):
		human_time = ' '.join(human_time.split(' ')[:max_segments])

	return human_time