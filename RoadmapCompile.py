import re
import sys
from datetime import timedelta, datetime, date
from collections import namedtuple, Counter
import operator
from operator import attrgetter, methodcaller
from time import gmtime, strftime
import sublime, sublime_plugin
from .roadmap_compiler_models import Task, Section, CategorySchedule, Statistics, DaySlot

def human_duration(total_duration, duration_categories_map, max_segments=5):
	groupped_duration = dict.fromkeys(duration_categories_map.keys(), 0)

	duration_categories = sorted(duration_categories_map.items(), key=operator.itemgetter(1), reverse=True)
	duration_categories = [d[0] for d in duration_categories]

	for duration_cat in duration_categories:
		groupped_duration[duration_cat] = total_duration // duration_categories_map[duration_cat]
		total_duration -= groupped_duration[duration_cat] * duration_categories_map[duration_cat]

	human_time = ' '.join(["%d%s" % (groupped_duration[cat], cat) for cat in duration_categories if groupped_duration[cat] > 0])

	# Cro out low precision (this should be smarte rounding)
	if max_segments < len(human_time.split(' ')):
		human_time = ' '.join(human_time.split(' ')[:max_segments])

	return human_time

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

class RoadmapCompile(sublime_plugin.TextCommand):
	HEADING_IDENTIFIER = '#'
	SECTION_IDENTIFIER = '## '
	INVALID_SECTIONS = [
		'## Summary',
		'## Effort planning',
		'## Trello warnings',
		'## Trello warnings ON',
	]
	

	def __section_indices(self, lines):
		SectionIndex = namedtuple('SectionIndex', ['index', 'is_valid'])
		indices = []
		for index, line in enumerate(lines):
			if line.startswith(self.HEADING_IDENTIFIER):
				is_valid_section = line.startswith(self.SECTION_IDENTIFIER) and \
					not line in self.INVALID_SECTIONS
				indices.append(SectionIndex(index, is_valid_section))
		indices.append(SectionIndex(len(lines), False))

		return indices

	def __extract_sections(self, content):

		array = content.split('\n')
		section_indices = self.__section_indices(array)

		sections = []

		for idx, sec_idx in enumerate(section_indices):
			if idx + 1 == len(section_indices): break
			start_idx = sec_idx.index
			end_idx = section_indices[idx+1].index

			is_section = sec_idx.is_valid
			section = Section(
				lines = array[start_idx:end_idx],
				is_valid = sec_idx.is_valid,
				row_at = start_idx
			)

			sections.append(section)

		return sections

	def __compute_total_weekly_load(self, section, statistics, for_weeks=40):
		tasks = [task for task in section.tasks for task in section.tasks]
		categorized_effort = self.__compute_weekly_load(tasks, statistics)
		total_effort = {}
		for key in categorized_effort:
			for week in categorized_effort[key]['effort']:
				if not week in total_effort:
					total_effort[week] = 0
				total_effort[week] += categorized_effort[key]['effort'][week]

		weekly_efforts = []
		for x in range(for_weeks):
			week = fmtweek(date.today() + timedelta(weeks=x))
			week_eff = total_effort[week] if week in total_effort else 0
			weekly_efforts.append(week_eff)

		return weekly_efforts


	def __update_section_timings(self, sections, edit, statistics):
		"""
		Below each section write a short summary of number of tasks and
		planned durations
		"""
		SPARK_START = "⌚"
		last_point = 0
		for section in sections:
			if section.is_valid:
				weekly_load = self.__compute_total_weekly_load(section, statistics)
				spark = sparkline(weekly_load)

				if section.needs_update:
					content = section.summary + '\n' + SPARK_START + spark
					line = self.view.line(self.view.find(section.lines[1], last_point, sublime.LITERAL))
					next_line = self.view.line(line.end() + 1)
					self.view.replace(edit, sublime.Region(line.begin(), next_line.end()), content)
				else:
					content = '\n' + section.summary + '\n' + SPARK_START + spark
					line = self.view.find(section.lines[0], last_point)
					self.view.insert(edit, line.end(), content)
				last_point = line.end()


	def __update_upcoming_tasks(self, sections, edit, statistics):
		"""
		Print the top upcoming tasks in the `### Upcoming tasks` section. 
		"""
		DEFAULT_NUM_TASKS = 10
		NUM_TASKS_PER_CATEGORY = 5
		UPCOMING_TASKS_SECTION_REGEX = '### (\d+\s)?[Uu]pcoming tasks'
		SHOW_TASKS_BY_CATEGORY = True

		index_section = self.view.find(UPCOMING_TASKS_SECTION_REGEX, 0)
		if index_section.begin() == -1:
			# Upcoming tasks section is not wanted. Stop.
			return

		# def upcoming_task_group_content(task_group, num_tasks):
		# 	sorted_tasks = sorted(task_group.tasks, key=attrgetter('urgency'))
		# 	sorted_tasks_string = '\n\n### ' + task_group.title + ' upcoming tasks\n\n' if task_group.show_title else ''
		# 	sorted_tasks_string += '\n'.join([str(task) for task in sorted_tasks[:num_tasks]])
		# 	return sorted_tasks_string


		def upcoming_cat_task_group_content(task_group, num_tasks, category):
			# print(category)
			# if category == 'All':
			# 	print (task_group.tasks)

			sorted_tasks = sorted(task_group.tasks, key=methodcaller('category_urgency', category))

			sorted_tasks_string = '\n\n### ' + task_group.title + ' upcoming tasks\n\n' if task_group.show_title else ''
			sorted_tasks_string += '\n'.join([str(task) for task in sorted_tasks[:num_tasks]])
			return sorted_tasks_string

		line = self.view.line(index_section)
		section_title = self.view.substr(line)
		match = re.search('(?P<num_tasks>\d+)', section_title)

		default_num_tasks = int(match.group('num_tasks')) if match else DEFAULT_NUM_TASKS

		nested_tasks = map(lambda section: section.tasks, sections)

		UpcomingTaskGroup = namedtuple('UpcomingTaskGroup', ['title', 'tasks', 'show_title'])

		all_tasks = [task for tasks in nested_tasks for task in tasks]
		upcoming_task_groups = [
			UpcomingTaskGroup(
				show_title = False,
				title = 'All',
				tasks = all_tasks
			)
		]

		for category in statistics.categories:
			upcoming_task_groups.append(UpcomingTaskGroup(
				title = category if category else 'Uncategorized',
				show_title = True,
				tasks = list(filter(lambda task: task.has_category(category), all_tasks))
			))

		all_task_groups_content = []
		for task_group in upcoming_task_groups:
			num_tasks = NUM_TASKS_PER_CATEGORY if task_group.show_title else default_num_tasks
			all_task_groups_content.append(upcoming_cat_task_group_content(task_group, num_tasks, task_group.title))

		next_section_index = self.view.find('^## ', line.end()).begin()
		replace_region = sublime.Region(line.end(), next_section_index)
		self.view.replace(edit, replace_region, '\n\n' + ''.join(all_task_groups_content) + '\n\n')

	def __all_categories(self, all_tasks):
		return set([task.meta.category for task in all_tasks])


	def __content_for_total_effort_chart(self, sections):
		durations = [section.duration[2] for section in sections]
		summed_durations = sum(
			(Counter(dict(x)) for x in durations),
			Counter())
		max_key_length = max([len(key) for key in summed_durations.keys()])
		max_value = max([value for value in summed_durations.values()])
		sorted_summed_durations = sorted(summed_durations.items(), key=operator.itemgetter(1), reverse=True)

		durations_chart = []
		scale_factor = 30/max_value
		for category, duration in sorted_summed_durations:
			hum_duration = human_duration(duration, Section.DURATION_MAP, max_segments=2)
			chart_format = "%" + str(max_key_length) + "s %6s %s"
			chart_row = chart_format % (category, hum_duration, "#" * int(duration * scale_factor))
			durations_chart.append(chart_row)

		effort_content = '```\n' + '\n'.join(durations_chart) + '\n```'
		return effort_content

	def __content_for_timeline(self, sections, statistics):
		FUTURE_DAYS = 30

		# nested_tasks = map(lambda section: section.tasks, sections)
		# all_tasks = [task for tasks in nested_tasks for task in tasks]

		# schedules = []
		# for category in statistics['categories_list']:
		# 	category_tasks = filter(lambda t: t.meta.category == category, all_tasks)
		# 	schedules.append(CategorySchedule(str(category), category_tasks, max_effort=8))

		# Print the schedules
		effort_content = '### Effort timeline\n'

		min_date = datetime.combine(date.today(), datetime.min.time())
		max_date = min_date + timedelta(days=FUTURE_DAYS)
		for schedule in schedules:
			effort_content += '\n\n*' + schedule.name + ':*\n'
			timeline = [0] * FUTURE_DAYS
			all_tasks = [task for task in schedule.tasks if task.scheduled_start_datetime is not None]

			# print(all_tasks, schedule.tasks)

			for task in all_tasks:
				if task.scheduled_start_datetime <= max_date and task.scheduled_end_datetime >= min_date:

					# Add first day
					amount = datetime.combine(task.scheduled_start_datetime, datetime.max.time()) - task.scheduled_start_datetime
					amount = amount.total_seconds() // 3600
					bucket = (task.scheduled_start_datetime - min_date).days
					timeline[bucket] += amount

					# Add last day effort
					if task.scheduled_start_datetime.date() != task.scheduled_end_datetime.date():
						amount = task.scheduled_end_datetime - datetime.combine(task.scheduled_end_datetime, datetime.min.time()) 
						amount = amount.total_seconds() // 3600
						bucket = (task.scheduled_end_datetime - min_date).days
						timeline[bucket] += amount

					if task.scheduled_end_datetime - task.scheduled_start_datetime > timedelta(days=2):
						cur_dt = task.scheduled_start_datetime + timedelta(days=1)
						while cur_dt.day < task.scheduled_end_datetime.day:
							amount = 24
							bucket = (cur_dt - min_date).days
							timeline[bucket] += amount
							cur_dt += timedelta(days=1)
						# print(task, task.scheduled_start_datetime, cur_dt, task.scheduled_end_datetime, task.duration)

					# Add all days in between

					# cur_dt = task.scheduled_start_datetime + timdelta(days=1)
					# while cur_dt <= task.scheduled_end_datetime:
					# 	# bucket = (task.scheduled_start_datetime - min_date).days
					# 	# timeline[bucket] = task.duration
					# 	cur_dt += timedelta(days=1)


			# print(timeline)

			timeline_chart = []
			scale_factor = 24/24  # 30 chars = 8 hrs of work
			for index, effort in enumerate(timeline):
				dateval = min_date + timedelta(days=(index+1))
				chart_row = "%s %s" % (dateval.strftime("%Y-%m-%d"), "#" * int(effort * scale_factor))
				timeline_chart.append(chart_row)

			effort_content += '```\n' + '\n'.join(timeline_chart) + '\n```'

		return effort_content

	def __update_planned_effort(self, sections, edit, statistics):
		effort_content = self.__content_for_total_effort_chart(sections)

		line = self.view.line(self.view.find('^### Total estimated effort', 0))
		next_section_index = self.view.find('^##', line.end()).begin()
		replace_region = sublime.Region(line.end(), next_section_index)
		self.view.replace(edit, replace_region, '\n\n' + effort_content + '\n\n')

	def __compute_statistics(self, sections):
		"""
		Computes statistics to avoid computing it several times later
		"""

		return Statistics(sections)


	def __estimate_missing_data(self, sections, stats):
		"""
		Fill-in the gaps: task duration.
		"""
		for section in sections:
			for task in section.tasks:
				for category in task.categories():
					if not task.category_duration(category, fake_valid=False):
						# print('update duration for %s to %d' % (category, stats.get_mean_duration(category)))
						task.set_fake_duration(category, stats.get_mean_duration(category))

	def __next_available_weekday(self, dt):
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


	def __schedule_task_with_deadline(self, task, available_before_date, available_effort, max_effort, category):
		"""
		The scheduler is only precise to the day,
		but will make sure you nevere have more than max_effort hours in 
		any single day
		"""
		MONDAY=0
		FRIDAY=4
		SATURDAY=5
		SUNDAY=6

		if available_effort <= 0:
			available_effort += max_effort
			available_before_date -= timedelta(days=1)

		# Define end_date
		if task.meta.end_date < available_before_date:
			end_date = task.meta.end_date
			available_effort = max_effort
		else:
			# print('SCHEDULE INFO: Task %s will have to begin earlier due to later tasks taking long' % (task,))
			end_date = available_before_date

		# TODO: print error if task was supposed to be finished before today
		# Skip saturday & sunday
		if end_date.weekday() == SATURDAY:
			end_date -= timedelta(days=1)
		elif end_date.weekday() == SUNDAY: # this should never really happen
			end_date -= timedelta(days=2)

		
		duration = int(task.category_duration(category))


		slots = []
		cur_dt = end_date
		while duration > 0:
			block_duration = min(available_effort, duration)
			slot = DaySlot(cur_dt, block_duration)
			slots.append(slot)
			available_effort -= block_duration
			duration -= block_duration
			if available_effort == 0:
				cur_dt = self.__next_available_weekday(cur_dt)
				available_effort = max_effort

		# if 'Sentinel Website' in str(task):
		# 	print(task, slots)
		task.set_slots_for_category(category, slots)

		return (cur_dt, available_effort)

	def __schedule_task_wout_deadline(self, task, first_available_date, max_effort, category, all_tasks):
		MONDAY=0
		FRIDAY=4
		SATURDAY=5
		SUNDAY=6

		duration = task.category_duration(category)

		# Don't plan work for weekends
		if first_available_date.weekday() == SATURDAY:
			first_available_date += timedelta(days=2)
		elif first_available_date.weekday() == SUNDAY: # this should never really happen
			first_available_date += timedelta(days=1)

		def next_available_weekday(dt):
			MONDAY=0
			FRIDAY=4
			SATURDAY=5
			SUNDAY=6
			if dt.weekday() == FRIDAY:
				delta = timedelta(days=3)
			elif dt.weekday() == SATURDAY:
				delta = timedelta(days=2)
			else:
				delta = timedelta(days=1)
			return dt + delta


		def available_effort(all_tasks, max_effort, cur_dt, category):
			
			def slots_of_day(slot, dt):
				return slot.date.date() == dt.date()

			cur_dt_date = cur_dt.date()

			day_slots = []
			for task in all_tasks:
				for slot in task.get_slots_for_category(category):
					if slot.date.date() == cur_dt_date:
						day_slots.append(slot)
						break

			allocated_effort = sum([slot.hours for slot in day_slots])
			return max_effort - allocated_effort

		slots = []
		cur_dt = first_available_date
		while duration > 0:
			remaing_effort = available_effort(all_tasks, max_effort, cur_dt, category)
			if remaing_effort == 0:
				cur_dt = next_available_weekday(cur_dt)
				continue

			allocate_effort = min(remaing_effort, duration)

			slot = DaySlot(cur_dt, int(allocate_effort))
			slots.append(slot)
			duration -= allocate_effort

			if duration > 0:
				cur_dt = next_available_weekday(cur_dt)


		task.set_slots_for_category(category, slots)

		return first_available_date

	def __compute_schedule_for_category(self, tasks, category, stats):
		"""
		End date is understood such, that max_load can be done also on that day
		"""
		max_load = stats.max_load_for_category(category)
		last_available_date = datetime(2999, 12, 12)
		remaing_effort = max_load
		tasks_w_deadline = list(filter(lambda t: t.meta.end_date is not None, tasks))
		tasks_wout_deadline = list(filter(lambda t: t.meta.end_date is None, tasks))

		tasks_w_deadline = sorted(tasks_w_deadline, key=attrgetter('meta.end_date'), reverse=True)

		for task in tasks_w_deadline:
			(last_available_date, remaing_effort) = self.__schedule_task_with_deadline(task, last_available_date, remaing_effort, max_load, category)

		first_available_date = datetime.combine(date.today(), datetime.min.time())
		remaing_effort = max_load # assume start at 0 (to avoid modifying schedule during the day)
		for task in tasks_wout_deadline:
			first_available_date = self.__schedule_task_wout_deadline(task, first_available_date, max_load, category, tasks)

		# schedules.append(CategorySchedule(str(category), category_tasks, max_effort=8))
	def __compute_schedule(self, sections, statistics):
		nested_tasks = map(lambda section: section.tasks, sections)
		all_tasks = [task for tasks in nested_tasks for task in tasks]

		schedules = []
		for category in statistics.categories:
			category_tasks = filter(lambda t: t.has_category(category), all_tasks)
			self.__compute_schedule_for_category(list(category_tasks), category, statistics)

	def __fold_links(self):
		startMarker = "]("
		endMarker = ")"
		startpos = self.view.find_all(startMarker, sublime.LITERAL)
		endpos = []

		validstartpos = []

		for x in range(len(startpos)):
			# Try to find end marker in the same line
			line = self.view.line(startpos[x])
			found = self.view.find(endMarker, startpos[x].end(), sublime.LITERAL)
			if line.end() >= found.end():
				validstartpos.append(startpos[x])
				endpos.append(found)

		regions = []
		for x in range(len(endpos)):
			regions.append(sublime.Region(validstartpos[x].end(), endpos[x].begin()))

		self.view.fold(regions)

	def __mark_date_completed(self, sections, edit):
		DATE_MARKER = "@done"
		STRIKE = "~~"
		for section in sections:
			for task in section.completed_tasks():
				if task.find(DATE_MARKER) == -1:
					end_pos = self.view.find(task, 0, sublime.LITERAL).end()
					formatted_marker = " %s(%s)" % (DATE_MARKER, date.today())
					self.view.insert(edit, end_pos, formatted_marker)
				if task.find(STRIKE) == -1:
					task_region = self.view.line(self.view.find(task, 0, sublime.LITERAL))
					self.view.insert(edit, task_region.begin() + 2, STRIKE)
					self.view.insert(edit, task_region.end() + 2, STRIKE)
					# print(task, task_region)
					# formatted_marker = " %s(%s)" % (DATE_MARKER, date.today())

	# def __draw_schedule(self, sections, edit, statistics, for_days=30):
	# 	nested_tasks = map(lambda section: section.tasks, sections)
	# 	all_tasks = [task for tasks in nested_tasks for task in tasks]

	# 	min_date = datetime.combine(date.today(), datetime.min.time())
	# 	max_date = min_date + timedelta(days=for_days)

	# 	effort_content = "\n\n"
	# 	for category in statistics.categories:
	# 		effort_content += category + '\n'
	# 		timeline = [0] * for_days
	# 		cat_tasks = [task for task in all_tasks if task.has_category(category)]
	# 		for task in cat_tasks:
	# 			for slot in task.get_slots_for_category(category):
	# 				if slot.date >= min_date and slot.date < max_date:
	# 					bucket = (slot.date - min_date).days
	# 					timeline[bucket] += slot.hours
	# 		effort_content += str(timeline) + '\n\n'

	# 	print(effort_content)
	# 	line = self.view.line(self.view.find('^### Weekly effort timeline', 0))
	# 	next_section_index = self.view.find('^##', line.end()).begin()
	# 	replace_region = sublime.Region(line.end(), next_section_index)
	# 	self.view.replace(edit, replace_region, effort_content)


	def __compute_weekly_load(self, tasks, statistics):
		effort = {}
		for category in statistics.categories:
			cat_effort = {
				'effort': {}
			}
			cat_tasks = [task for task in tasks if task.has_category(category)]
			for task in cat_tasks:
				for slot in task.get_slots_for_category(category):
					week = fmtweek(slot.date)
					if not week in cat_effort['effort']:
						cat_effort['effort'][week] = 0

					cat_effort['effort'][week] += slot.hours

			effort[str(category)]=cat_effort
		return effort

	def __draw_weekly_schedule(self, sections, edit, statistics):

		heading_region = self.view.find('^### (\d+ )?Week(.+) effort timeline', 0)
		if heading_region.begin() == -1:
			return

		line = self.view.line(heading_region)

		match = re.search('(?P<num_weeks>\d+)', self.view.substr(line))
		for_weeks = int(match.group('num_weeks')) if match else 10

		nested_tasks = map(lambda section: section.tasks, sections)
		all_tasks = [task for tasks in nested_tasks for task in tasks]

		effort = self.__compute_weekly_load(all_tasks, statistics)

		max_weekly_effort = 0
		for key in effort:
			for week in effort[key]['effort']:
				max_weekly_effort = max(max_weekly_effort, effort[key]['effort'][week])

		effort_content = '{:<7}  '.format('')
		effort_content += "".join(["{:<5}  ".format(cat) for cat in statistics.categories ]) + '\n'
		
		max_chars = 5
		for x in range(for_weeks):
			dt = date.today() + timedelta(weeks=x)
			week = fmtweek(dt)
			effort_content += '{:<7}  '.format(week)
			for category in statistics.categories:
				if week in effort[category]['effort']:
					week_eff = '|' * (round(effort[category]['effort'][week] / max_weekly_effort * max_chars))
				else:
					week_eff = ''
				effort_content += '{:<5}  '.format(week_eff)
			effort_content += '\n'

		next_section_index = self.view.find('^##', line.end()).begin()
		replace_region = sublime.Region(line.end(), next_section_index)
		self.view.replace(edit, replace_region, '\n\n```\n' + effort_content + '```\n\n')

	def __draw_section_schedule(self, sections, edit, statistics, to_scale=False):

		heading_region = self.view.find('^### (\d+w )?[Ss]ection schedule', 0)
		if heading_region.begin() == -1:
			return

		line = self.view.line(heading_region)

		match = re.search('(?P<num_weeks>\d+).+', self.view.substr(line))
		for_weeks = int(match.group('num_weeks')) if match else 30
		for_weeks = min(for_weeks, 60)

		match = re.search('.+(?P<to_scale>to scale)\s*', self.view.substr(line))
		to_scale = True if match and match.group('to_scale') else to_scale

		MAX_WIDTH = 76
		title_width = MAX_WIDTH - for_weeks - 1
		fmt_string = '{:<' + str(title_width) + '} {}\n'


		data = []
		smallest = 0
		largest = 40
		for section in sections:
			if section.is_valid:
				weekly_load = self.__compute_total_weekly_load(section, statistics, for_weeks=for_weeks)
				largest = max(largest, max(weekly_load))
				data.append((weekly_load, section.title[3:]))

		if not to_scale:
			largest = 40

		effort_content = ''
		for x in range(len(data)):
			weekly_load, section_title = data[x]
			spark = sparkline(weekly_load, smallest=smallest, largest=largest)
			effort_content += fmt_string.format(truncate_middle(section_title, title_width), spark)

		next_section_index = self.view.find('^##', line.end()).begin()
		replace_region = sublime.Region(line.end(), next_section_index)
		self.view.replace(edit, replace_region, '\n\n```\n' + effort_content + '```\n\n')

	def __update_timestamp(self, edit):

		heading_region = self.view.find('^# Roadmap', 0)
		if heading_region.begin() == -1:
			return

		line = self.view.line(heading_region)

		next_section_index = self.view.find('^##', line.end()).begin()
		replace_region = sublime.Region(line.end(), next_section_index)
		content = 'Last updated: {}'.format(datetime.now().strftime("%Y-%m-%d"))
		self.view.replace(edit, replace_region, '\n\n' + content + '\n\n')

	def run(self, edit):		
		content=self.view.substr(sublime.Region(0, self.view.size()))
		sections = self.__extract_sections(content)
		statistics = self.__compute_statistics(sections)
		self.__estimate_missing_data(sections, statistics)
		self.__compute_schedule(sections, statistics)

		self.__mark_date_completed(sections, edit)
		self.__update_section_timings(sections, edit, statistics)
		self.__update_upcoming_tasks(sections, edit, statistics)
		self.__update_planned_effort(sections, edit, statistics)
		self.__draw_weekly_schedule(sections, edit, statistics)
		self.__draw_section_schedule(sections, edit, statistics)
		self.__update_timestamp(edit)

		self.__fold_links()
