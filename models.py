import re
import sys
from datetime import timedelta, datetime, date
from collections import namedtuple, Counter
from operator import attrgetter, itemgetter
import sublime, sublime_plugin
from .utils import human_duration, mean, extract_task_metadata

class DaySlot(object):
	"""WorkDay(date, hours)"""
	def __init__(self, date, hours):
		self.date = date
		self.hours = hours
		
	def __repr__(self):
		return "%s - %d hours" % (self.date, self.hours)

class Task(object):
	"""Task(raw)"""

	DATE_FORMAT = '%Y-%m-%d'

	def __init__(self, raw, section):
		
		def extract_description(task):
			TASK_META_MATCH_REGEX = '\[(?P<flags>M\s?)?(?P<categories>(\d+\w\s?)?(\w+)?(\w+\s\d+\w\s?)*)(?P<end_date>\d{4}-\d{2}-\d{2})?\]$'
			meta_index = re.search(TASK_META_MATCH_REGEX, task)
			if meta_index:
				description = task[2:meta_index.start()]
			else:
				description = task[2:]

			return description.strip()

		self._raw = raw 
		self._meta, self._raw_meta = extract_task_metadata(raw)
		self._description = extract_description(raw)
		self._scheduled_start_datetime = None
		self._scheduled_end_datetime = None
		self._fake_duration = {}
		self.slots = {}
		self._section = section

	@property
	def section(self):
	    return self._section


	@property
	def is_trello_card(self):
		CARD_ID_REGEX = 'https\:\/\/trello\.com\/c\/(?P<card_id>.+)\/'
		return re.search(CARD_ID_REGEX, self.raw) is not None

	@property
	def trello_url(self):
	    CARD_ID_REGEX = 'https\:\/\/trello\.com\/c\/(?P<card_id>.+)\/'
	    return re.search(CARD_ID_REGEX, self.raw).group(0)

	@property
	def trello_id(self):
		CARD_ID_REGEX = 'https\:\/\/trello\.com\/c\/(?P<card_id>.+)\/'
		match = re.search(CARD_ID_REGEX, self.raw)
		if not match: return None  
		return match.group('card_id')

	def set_slots_for_category(self, category, slots):
		self.slots[category] = slots

	def get_slots_for_category(self, category):
		return self.slots[category] if category in self.slots else []

	@property
	def is_mandatory(self):
		return not self.meta.optional

	@property
	def start_date(self):
		"""
		It's the date when it needs to start to be finished on time.
		For tasks with multiple categories, it the earliest start_time for
		all categories
		"""
		max_duration = max([self.category_duration(category) for category in self.categories()])
		if self.meta.end_date and max_duration:
			return self.meta.end_date - timedelta(hours = max_duration)
		return None

	def has_category(self, category):
		return category in self.meta.categories.keys()
	
	@property
	def scheduled_start_datetime(self):
	    return self._scheduled_start_datetime

	@scheduled_start_datetime.setter
	def scheduled_start_datetime(self, value):
		self._scheduled_start_datetime = value

	@property
	def scheduled_end_datetime(self):
	    return self._scheduled_end_datetime

	@scheduled_end_datetime.setter
	def scheduled_end_datetime(self, value):
		self._scheduled_end_datetime = value

	@property
	def has_deadline(self):
		return self.meta.end_date is not None
	
	@property
	def description(self):
		return self._description

	@property
	def total_duration(self, fake_valid=True):
		print('TODO: to be reimplemented: task.duration')
		if self.meta.duration_value is not None and self.meta.duration_unit is not None:
			return self.meta.duration_value * Section.DURATION_MAP[self.meta.duration_unit]
		elif fake_valid:
			return sum([self._fake_duration[key] for key in self._fake_duration.keys()])
		else:
			return None

	def categories(self):
		return list(self.meta.categories.keys())

	def category_duration(self, category, fake_valid=True):
		if category in self.categories():
			if self.meta.categories[category]['duration_value'][0] is not None and self.meta.categories[category]['duration_unit'] is not None:
				return self.meta.categories[category]['duration_value'][0] * Section.DURATION_MAP[self.meta.categories[category]['duration_unit']]
			elif fake_valid and category in self._fake_duration:
				return self._fake_duration[category]
			else:
				return None
		else:
			print ('TODO: what if the category is not provided')
			return None
	
	@total_duration.setter
	def total_duration(self, value):
		self._fake_duration = value

	def set_fake_duration(self, category, value):
		self._fake_duration[category] = value

	@property
	def raw_meta(self):
		return self._raw_meta

	@property
	def urgency(self):
		NO_DATE_DEFAULT_URGENCY = sys.maxsize - 1
		PAST_DATE_URGENCY_MULTIPLIER = 2
		if self.start_date:
			if self.start_date.date() < date.today():
				return self.start_date.timestamp() * PAST_DATE_URGENCY_MULTIPLIER
			else:
				return self.start_date.timestamp()
		else:
			return NO_DATE_DEFAULT_URGENCY
		return 2

	def category_urgency(self, category):
		PAST_DATE_URGENCY_MULTIPLIER = 2

		def urgency_normalizer(start):
			if start.date() < date.today():
				return start.timestamp() * PAST_DATE_URGENCY_MULTIPLIER 
			else:
				return start.timestamp()

		if category == 'All':
			# Take largest urgency from all categories
			# Use overall 
			cats_deadlines = [self.get_slots_for_category(cat)[0].date for cat in self.meta.categories.keys()]
			return urgency_normalizer(max(cats_deadlines))
		elif not self.has_category(category):
			return 0 # just in case
		else:
			return urgency_normalizer(self.get_slots_for_category(category)[0].date)

	@property
	def raw(self):
		return self._raw

	@property
	def meta(self):
		return self._meta

	def __str__(self):
		return "- %s %s" % (self.description, self.raw_meta)

	def __repr__(self):
		return str(self)
			

class Section(object):
	'Section(lines, is_valid)'

	TASK_IDENTIFIER = '- '
	COMPLETED_TASK_IDENTIFIER = '+ '
	# TASK_META_REGEX = '\[(\w{3})?\s?(?:(\d{1,})(h|d|w|m|q))?\s?(\d{4}-\d{2}-\d{2}.*)?\]'
	DURATION_MAP = {
		'h': 1,
		'd': 8,
		'w': 40,
		'm': 168,
		# 'q': 504
	}

	def __init__(self, lines, is_valid, row_at):
		'Create new instance of Section(lines, is_valid)'

		self._lines = lines
		self._is_valid = is_valid
		self._row_at = row_at

		all_tasks = [Task(raw_task, self) for raw_task in self.raw_tasks if self.is_valid]
		self._all_tasks = all_tasks
		self._tasks = [task for task in all_tasks if task.is_mandatory]
		weight_regex = '\((?P<weight>\d+(\.\d+)?)x\)'
		priority_match = re.search(weight_regex, self.lines[0])
		self._weight = float(priority_match.group('weight')) if priority_match else 1

	@property
	def title(self):
		title = self.lines[0]
		if re.search('\(.+\)', title):
			title = re.search('(?P<title>.+)(\s?\(.+\))', title).group('title').strip()
		return title

	@property
	def weight(self):
	    return self._weight

	@property
	def lines(self):
		return self._lines

	@property
	def is_valid(self):
		return self._is_valid

	@property
	def row_at(self):
		return self._row_at

	@property
	def needs_update(self):
		return self.lines[1].startswith('[')

	@property
	def summary(self):
		return "[%d tasks, %s]" % (self.num_mandatory_tasks, self.smart_duration)

	@property
	def smart_duration(self):
		(known_duration, untagged_count, category_durations) = self.duration
		sorted_cat_durs = sorted(category_durations.items(), key=itemgetter(1), reverse=True)
		
		if known_duration > 0:
			str = human_duration(known_duration, self.DURATION_MAP)
			str += ' (' + ', '.join(["%s %s" % (dur[0], human_duration(dur[1], self.DURATION_MAP, max_segments=2)) for dur in sorted_cat_durs]) + ')'
			if untagged_count > 0:
				str += " + %d tasks with missing duration" % untagged_count
		else:
			str = "Missing duration metadata"

		return str

	@property
	def raw_tasks(self):
		is_task = lambda line: line.startswith(self.TASK_IDENTIFIER)
		return [line for line in self.lines if is_task(line)]

	def completed_tasks(self):
		is_completed_task = lambda line: line.startswith(self.COMPLETED_TASK_IDENTIFIER)
		return [line for line in self.lines if is_completed_task(line)]

	@property
	def tasks(self):
		return self._tasks

	@property
	def all_tasks(self):
	    return self._all_tasks

	@property
	def duration(self):
		total_duration = 0 # lowest units based on DURATION_MAP
		untagged_with_duration = 0

		categ_durations = {}

		mandatory_tasks = [task for task in self.tasks if task.is_mandatory]
		for task in mandatory_tasks:
			categories = task.categories()
			for category in task.categories():
				total_duration += task.category_duration(category)
				if not category in categ_durations:
					categ_durations[category] = 0
				categ_durations[category] += task.category_duration(category)

		return (total_duration, untagged_with_duration, categ_durations)

	def __str__(self):
		return "<Section '%s', %d items, valid:%s>" % (self.title, self.num_mandatory_tasks, self.is_valid)

	def __repr__(self):
		return str(self)

	@property
	def num_tasks(self):
		# Counts lines starting with Task delimeter
		if not self.is_valid: return 0
		return len(self.tasks)

	@property
	def num_mandatory_tasks(self):
		# Counts lines starting with Task delimeter
		if not self.is_valid: return 0
		return len([task for task in self.tasks if task.is_mandatory])

	def __lt__(self, other):
		return self.title < other.title

class CategorySchedule(object):
	"""Schedule(tasks)"""
	def __init__(self, name, tasks, max_effort=8):
		self._name = name
		self._tasks = list(tasks)
		self._max_effort = max_effort
		self._generate_schedule(self._tasks, max_effort)

	@property
	def name(self):
	    return self._name

	@property
	def tasks(self):
	    return self._tasks
	

	def _generate_schedule(self, tasks, max_effort):
		"""
		In this function, datetime and such receive the maening of a work_hour
		and not full hours.
		"""

		if max_effort > 24:
			print ("ERROR: Maximum max_effort per category is 24 hours. Results will be incorrect")

		last_empty_timeslot = datetime(2999, 12, 12)
		tasks_w_deadline = filter(lambda t: t.meta.end_date is not None, tasks)
		tasks_wout_deadline = filter(lambda t: t.meta.end_date is None, tasks)

		def count_weekends_between(start_date, end_date):
			"""
			Count number of time a friday has gone to a saturday in the given
			dates
			"""
			
			if start_date.date() == end_date.date():
				return 0

			weekdays = 0
			cur_dt = start_date
			end_dt = end_date - timedelta(days=1)
			while cur_dt <= end_dt:
				if cur_dt.weekday() == 4 and (cur_dt + timedelta(days=1)).weekday() == 5:
					weekdays+=1
				cur_dt = cur_dt + timedelta(days=1)

			# fm = "%c"
			# print("%d weekdays between %s and %s" % (weekdays, format(start_date, fm), format(end_date, fm)))

			return weekdays

		# Place tasks with deadline, starting from the last to the first
		sorted_tasks_w_deadline = sorted(tasks_w_deadline, key=attrgetter('meta.end_date'), reverse=True)

		for task in sorted_tasks_w_deadline:
			# Setting the task's scheduled_end_datetime
			if task.meta.end_date < last_empty_timeslot:
				task.scheduled_end_datetime = datetime.combine(task.meta.end_date, datetime.max.time())
			else:
				print('SCHEDULE INFO: Task %s will have to begin earlier due to later tasks taking long' % (task,))
				task.scheduled_end_datetime = last_empty_timeslot

			# duration defined in work-hours
			scaled_hours = task.duration * (24.0 / max_effort)

			time_to_subtract = timedelta(hours=scaled_hours)


			# Weekends are non-working: extend the schedule
			num_weekends = count_weekends_between(task.scheduled_end_datetime - time_to_subtract, task.scheduled_end_datetime)
			time_to_subtract += timedelta(days=2*num_weekends)

			task.scheduled_start_datetime = task.scheduled_end_datetime - time_to_subtract
			last_empty_timeslot = task.scheduled_start_datetime

		today_start = datetime.combine(date.today(), datetime.min.time())
		# print(last_empty_timeslot, today_start)
		if last_empty_timeslot < today_start:
			print('SCHEDULE WARNING: The schedule is set to start before today (%s is set to begin %s)' % ( sorted_tasks_w_deadline[-1], sorted_tasks_w_deadline[-1].scheduled_start_datetime))


		def get_unnalocated_time_at(date, tasks):
			# TODO: implement
			return 1
			# pass

		# TODO:Place tasks without deadline, in default order
		# TODO: sort by urgency anyway
		# last_unallocated_day = today_start
		# for task in tasks_wout_deadline:
		# 	duration_left = task.duration * (24.0 / max_effort)
		# 	start_dt = None
		# 	end_dt = None
		# 	while duration_left > 0:
		# 		time_left = get_unnalocated_time_at(last_unallocated_day, tasks)
		# 		if time_left > 0 and start_dt is None:
		# 			start_dt = last_unallocated_day
		# 		allocate_today = 24 - time_left
		# 		if allocate_today > 0:
		# 			print('TODO: Do partial allocation for today')
		# 		last_unallocated_day += timedelta(days=1)
		# 		duration_left -= 1 
	


	# To create schedule for single cateroy:
	# Place all tasks with deadline onto timeline wrt to the constraints of that category (max work for that category per day) - start at the last task
		# print errors if start_time of any tasks becomes earlier than today
	# Place all remaining tasks in order from beginning till the end	




class Statistics(object):
	"""Statistics(sections)"""
	def __init__(self, sections):
		self.sections = sections
		self.all_tasks = self._compute_alltasks(sections)
		self.categories = self._compute_categories()
		self.category_means = self._compute_category_means()

	def _compute_alltasks(self, sections):
		nested_tasks = map(lambda section: section.tasks, sections)
		return [task for tasks in nested_tasks for task in tasks]

	def _compute_categories(self):
		all_categories = [list(task.meta.categories.keys()) for task in self.all_tasks]
		all_categories = [cat for cats in all_categories for cat in cats]
		return sorted(list(set(all_categories)))

	def max_load_for_category(self, category):
		conf = sublime.load_settings('project_planner.sublime-settings')
		default_workload = conf.get('default_daily_category_workload')
		overrides = conf.get("category_workloads", [])

		workload = next((x['workload'] for x in overrides if x['name'] == category), default_workload)


		return workload # 8 hours

	def _compute_category_means(self):
		# Compute mean and median duration of each category's task
		stats = {}

		means = []
		for category in self.categories:
			filtered_tasks = filter(lambda t: t.has_category(category), self.all_tasks)
			durations = [task.category_duration(category, fake_valid=False) for task in filtered_tasks]
			duros = [dur for dur in durations if dur is not None]
			stats[str(category)] = mean(duros)
			means.append(mean(duros))

		# If any category has None stats, use global mean/median
		none_means = [index for index in range(len(means)) if means[index] is None]
		overall_mean = mean([mean for mean in means if mean is not None])

		for none_mean in none_means:
			stats[str(self.categories[none_mean])] = overall_mean

		return stats

	def get_mean_duration(self, category):
		return self.category_means[category]
		
