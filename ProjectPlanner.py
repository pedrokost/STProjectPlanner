import re
import os
import sys
from datetime import timedelta, datetime, date
from collections import namedtuple, Counter
import operator
import math
from operator import attrgetter, methodcaller, itemgetter
from time import gmtime, strftime
import random
import sublime
import sublime_plugin
import mdpopups
from .models import Task, Section, Statistics, DaySlot
from .models import human_duration
from .utils import sparkline, truncate_middle, weeknumber, fmtweek
from .utils import next_available_weekday, human_duration, weighted_sampling_without_replacement
from .utils import listdiff


class ProjectPlannerCompile(sublime_plugin.TextCommand):
    HEADING_IDENTIFIER = '#'
    SECTION_IDENTIFIER = '## '
    INVALID_SECTIONS = [
        '## Trello warnings',
        '## Plan:*',
    ]

    def __section_indices(self, lines):
        SectionIndex = namedtuple('SectionIndex', ['index', 'is_valid'])
        indices = []

        def is_section_valid(line):
            if not line.startswith(self.SECTION_IDENTIFIER):
                return False
            for invalid_section in self.INVALID_SECTIONS:
                if re.match(invalid_section, line):
                    return False
            return True

        for index, line in enumerate(lines):
            if line.startswith(self.HEADING_IDENTIFIER):
                indices.append(SectionIndex(index, is_section_valid(line)))
        indices.append(SectionIndex(len(lines), False))

        return indices

    def _extract_sections(self, content):

        array = content.split('\n')
        section_indices = self.__section_indices(array)

        sections = []

        for idx, sec_idx in enumerate(section_indices):
            if idx + 1 == len(section_indices):
                break
            start_idx = sec_idx.index
            end_idx = section_indices[idx + 1].index

            is_section = sec_idx.is_valid
            section = Section(
                lines=array[start_idx:end_idx],
                is_valid=sec_idx.is_valid,
                row_at=start_idx
            )

            sections.append(section)

        return sections

    def _compute_total_weekly_load(self, section, statistics, for_weeks=40, quarter_breaks=False):
        tasks = [task for task in section.tasks]
        categorized_effort = self.__compute_weekly_load(tasks, statistics)

        total_effort = {}
        for key in categorized_effort:
            for week in categorized_effort[key]['effort']:
                if not week in total_effort:
                    total_effort[week] = 0
                total_effort[week] += categorized_effort[key]['effort'][week]

        weekly_efforts = []
        QUARTER_CHANGE_DELIMETER = None
        prev_quarter = None
        for x in range(for_weeks):
            dt = date.today() + timedelta(weeks=x)
            new_quarter = math.ceil(dt.month / 3.)
            if quarter_breaks and prev_quarter and prev_quarter != new_quarter:
                weekly_efforts.append(QUARTER_CHANGE_DELIMETER)
            week = fmtweek(dt)
            week_eff = total_effort[week] if week in total_effort else 0
            weekly_efforts.append(week_eff)
            prev_quarter = new_quarter

        return weekly_efforts

    def _update_section_timings(self, sections, edit, statistics):
        """
        Below each section write a short summary of number of tasks and
        planned durations
        """
        
        SPARK_START = "⌚"
        last_point = 0
        for section in sections:
            if section.is_valid:
                weekly_load = self._compute_total_weekly_load(section, statistics, quarter_breaks=self.show_quarters)
                spark = sparkline(weekly_load)

                if section.needs_update:

                    content = section.summary + '\n' + SPARK_START + spark

                    # Handle for weird Markdown plugin (unknown) inserting newlines
                    # after heading
                    search_line = section.lines[1]
                    if len(section.lines) > 2 and section.lines[1] == '':
                        search_line = section.lines[2]

                    line = self.view.line(self.view.find(search_line, last_point, sublime.LITERAL))
                    next_line = self.view.line(line.end() + 1)
                    region = sublime.Region(line.begin(), next_line.end())

                    self.view.replace(edit, region, content)
                    last_point = line.begin() + len(content)
                else:
                    content = '\n' + section.summary + '\n' + SPARK_START + spark
                    line = self.view.find(section.lines[0], last_point, sublime.LITERAL)
                    self.view.insert(edit, line.end(), content)
                    last_point = line.begin() + len(content)
                # last_point = line.end()

    def _update_upcoming_tasks(self, sections, edit, statistics):
        """
        Print the top upcoming tasks in the `## Plan: Upcoming tasks` section. 
        """
        DEFAULT_NUM_TASKS = 10
        NUM_TASKS_PER_CATEGORY = 5
        UPCOMING_TASKS_SECTION_REGEX = '## Plan: (\d+\s)?[Uu]pcoming tasks'
        SHOW_TASKS_BY_CATEGORY = True

        sections = [section for section in sections if section.weight > 0]

        index_section = self.view.find(UPCOMING_TASKS_SECTION_REGEX, 0)
        if index_section.begin() == -1:
            # Upcoming tasks section is not wanted. Stop.
            return

        def upcoming_cat_task_group_content(task_group, num_tasks, category):
            sorted_tasks = sorted(task_group.tasks, key=methodcaller('scheduled_start_date', category))

            sorted_tasks_string = '\n\n### ' + task_group.title + ' upcoming tasks\n\n' if task_group.show_title else ''
            sorted_tasks_string += '\n'.join([str(task) for task in sorted_tasks[:num_tasks]])

            if len(sorted_tasks) == 0:
                sorted_tasks_string += 'There are not tasks in this category'

            return sorted_tasks_string

        line = self.view.line(index_section)
        section_title = self.view.substr(line)
        match = re.search('(?P<num_tasks>\d+)', section_title)

        default_num_tasks = int(match.group('num_tasks')) if match else DEFAULT_NUM_TASKS

        nested_tasks = [section.tasks for section in sections]

        UpcomingTaskGroup = namedtuple('UpcomingTaskGroup', ['title', 'tasks', 'show_title'])

        all_tasks = [task for tasks in nested_tasks for task in tasks if task.is_mandatory]
        upcoming_task_groups = [
            UpcomingTaskGroup(
                show_title=False,
                title='All',
                tasks=all_tasks
            )
        ]

        for category in statistics.categories:
            upcoming_task_groups.append(UpcomingTaskGroup(
                title=category if category else 'Uncategorized',
                show_title=True,
                tasks=list(filter(lambda task: task.has_category(category), all_tasks))
            ))

        upcoming_task_groups.append(UpcomingTaskGroup(
            title='Deadlined',
            show_title=True,
            tasks=list(filter(lambda task: task.has_deadline, all_tasks))
        ))

        all_task_groups_content = []
        for task_group in upcoming_task_groups:
            num_tasks = NUM_TASKS_PER_CATEGORY if task_group.show_title else default_num_tasks
            all_task_groups_content.append(upcoming_cat_task_group_content(task_group, num_tasks, task_group.title))

        next_section_index = self.view.find('^## ', line.end()).begin()
        replace_region = sublime.Region(line.end(), next_section_index)
        self.view.replace(edit, replace_region, '\n\n' + ''.join(all_task_groups_content) + '\n\n')

    def _content_for_total_effort_chart(self, sections):
        durations = [section.duration[2] for section in sections]
        summed_durations = sum(
            (Counter(dict(x)) for x in durations),
            Counter())
        max_key_length = max([len(key) for key in summed_durations.keys()])
        max_value = max([value for value in summed_durations.values()])
        sorted_summed_durations = sorted(summed_durations.items(), key=itemgetter(1), reverse=True)

        durations_chart = []
        scale_factor = 30 / max_value
        for category, duration in sorted_summed_durations:
            hum_duration = human_duration(duration, Section.DURATION_MAP, max_segments=2)
            chart_format = "%" + str(max_key_length) + "s %6s %s"
            chart_row = chart_format % (category, hum_duration, "#" * int(duration * scale_factor))
            durations_chart.append(chart_row)

        effort_content = '```\n' + '\n'.join(durations_chart) + '\n```'
        return effort_content

    def _update_planned_effort(self, sections, edit, statistics):

        heading_region = self.view.find('^## Plan: Total estimated effort', 0)
        if heading_region.begin() == -1:
            return

        effort_content = self._content_for_total_effort_chart(sections)

        line = self.view.line(heading_region)
        next_section_index = self.view.find('^##', line.end()).begin()
        replace_region = sublime.Region(line.end(), next_section_index)
        self.view.replace(edit, replace_region, '\n\n' + effort_content + '\n\n')

    def _compute_statistics(self, sections):
        """
        Computes statistics to avoid computing it several times later
        """

        return Statistics(sections)

    def _estimate_missing_data(self, sections, stats):
        """
        Fill-in the gaps: task duration.
        """
        for section in sections:
            for task in section.tasks:
                for category in task.categories():
                    if not task.category_duration(category, fake_valid=False):
                        task.set_fake_duration(category, stats.get_mean_duration(category))

    def _schedule_task_with_deadline(self, task, available_before_date, available_effort, max_effort, category):
        """
        The scheduler is only precise to the day,
        but will make sure you nevere have more than max_effort hours in 
        any single day
        """
        MONDAY = 0
        FRIDAY = 4
        SATURDAY = 5
        SUNDAY = 6

        if available_effort <= 0:
            available_effort += max_effort
            available_before_date -= timedelta(days=1)

        # Define end_date
        if task.meta.end_date is not None and task.meta.end_date < available_before_date:
            end_date = task.meta.end_date
            available_effort = max_effort
        else:
            # print('SCHEDULE INFO: Task %s will have to begin earlier due to later tasks taking long' % (task,))
            end_date = available_before_date

        # Skip saturday & sunday
        if end_date.weekday() == SATURDAY:
            end_date -= timedelta(days=1)
        elif end_date.weekday() == SUNDAY:  # this should never really happen
            end_date -= timedelta(days=2)

        if end_date < datetime.today():
            self.add_error('Past deadline', '"{}" ({}) should have been completed by {}'.format(
                task.description, category, end_date.date()))

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
                cur_dt = next_available_weekday(cur_dt)
                available_effort = max_effort

        task.set_slots_for_category(category, slots)

        return (cur_dt, available_effort)

    def _schedule_preconditioned_task(self, task, first_available_date, max_effort, category, all_tasks, prev_deadlined_task, next_deadlined_task):

        print('Place "{} after "{}" but before "{}" in category "{}"'.format(
            task.name, prev_deadlined_task, next_deadlined_task, category))
        return first_available_date

    def _schedule_task_wout_deadline(self, task, first_available_date, max_effort, category, all_tasks, completed_before_date=None):
        MONDAY = 0
        FRIDAY = 4
        SATURDAY = 5
        SUNDAY = 6

        duration = task.category_duration(category)

        # Don't plan work for weekends
        if first_available_date.weekday() == SATURDAY:
            first_available_date += timedelta(days=2)
        elif first_available_date.weekday() == SUNDAY:  # this should never really happen
            first_available_date += timedelta(days=1)

        def next_available_weekday(dt):
            MONDAY = 0
            FRIDAY = 4
            SATURDAY = 5
            SUNDAY = 6
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

        if completed_before_date is not None and completed_before_date < cur_dt:
            self.add_error('Prerequirement mismatch', '{}: "{}" should have been completed before {}. Instead it will be done by {}'.format(
                category, task.description, task.prerequirement_for_deadlined, cur_dt.date()))

        task.set_slots_for_category(category, slots)

        # FIXME: It it be returning the cur_dt?
        return first_available_date

    def _prioritize_tasks(self, tasks_wout_deadline, stats):
        """
        reoader tasks_wout_deadline based on the section probabilitisc
        weights
        """

        def adjusted_section_weights(sections, section_countdown):
            weights = []
            for section in sections:
                weight = section.weight if section_countdown[section.title] > 0 else 0
                weights.append((weight, section))
            return weights

        sections = set([task.section for task in tasks_wout_deadline])
        sections = sorted(list(sections))  # order the set, for limited randomness

        section_tasks = {}
        section_countdown = {}
        # tot_tasks = 0
        for section in sections:
            section_tasks[section.title] = [
                task for task in section.tasks if task.is_mandatory and task in tasks_wout_deadline]
            section_countdown[section.title] = len(section_tasks[section.title])
            # tot_tasks += len(section_tasks[section.title])
        # assert(len(tasks_wout_deadline) == tot_tasks)
        prioritized_tasks = []
        weighted_sections = adjusted_section_weights(sections, section_countdown)

        def get_next_task_in_section(section, rem_tsks, section_countdown):
            valid_tasks = section_tasks[section.title]
            return valid_tasks[len(valid_tasks) - section_countdown[section.title]]

        remaining_tasks = list(tasks_wout_deadline)
        while len(remaining_tasks) > 0:
            myrandom = random.Random(self.myrandomseed)
            section = weighted_sampling_without_replacement(weighted_sections, 1, myrandom)[0][1]
            task = get_next_task_in_section(section, remaining_tasks, section_countdown)
            # print('Selecting task %s from %s' % (task, section))
            prioritized_tasks.append(task)
            remaining_tasks.remove(task)
            section_countdown[task.section.title] -= 1
            weighted_sections = adjusted_section_weights(sections, section_countdown)

        return prioritized_tasks

    def _check_correct_deadlined_task_ordering(self, tasks, category):
        # group tasks by group
        sections = list(set([t.section for t in tasks]))

        for section in sections:
            section_tasks = [t for t in tasks if t.section == section]

            for i in range(len(section_tasks) - 1):
                if section_tasks[i].meta.end_date > section_tasks[i + 1].meta.end_date:
                    self.add_error(
                        'Incorrect ordering of tasks with deadline',
                        '{}: Task *{}* with deadline {} should be placed after task *{}* with deadline {} '.format(
                            section.pretty_title,
                            section_tasks[i].description,
                            section_tasks[i].meta.end_date.date(),
                            section_tasks[i + 1].description,
                            section_tasks[i + 1].meta.end_date.date()
                        )
                    )

    def _compute_schedule_for_category(self, tasks, category, stats):
        """
        End date is understood such, that max_load can be done also on that day

        Steps:
        1. Place all deadlined tasks the latest possible - ensure deadlines OK
        2. Place all prerequisites the soonest possible - ensure deadlines OK
        3. Place everything else based on priorities - play with fire

        Thus 2 levels of errors:
        CRITICAL: deadlined task cannot be finished
        SEVERE: preconditioned task cannot be finished 
        """

        max_load = stats.max_load_for_category(category)
        last_available_date = datetime(2999, 12, 12)
        remaing_effort = max_load
        tasks_w_deadline = [t for t in tasks if t.meta.end_date is not None]

        self._check_correct_deadlined_task_ordering(tasks_w_deadline, category)

        tasks_w_deadline = sorted(tasks_w_deadline, key=attrgetter('meta.end_date'), reverse=True)
        tasks_wout_deadline = list(filter(lambda t: t.meta.end_date is None, tasks))
        tasks_wout_deadline = self._prioritize_tasks(tasks_wout_deadline, stats)
        tasks_preconditioned = []

        num_tasks_w_deadline = len(tasks_w_deadline)

        for task in tasks_w_deadline:
            (last_available_date, remaing_effort) = self._schedule_task_with_deadline(
                task, last_available_date, remaing_effort, max_load, category)

        # Step 2: Place all prerequisites the soonest possible
        # First, find all prerequisites tasks
        for task in tasks_wout_deadline:
            prerequirement_for = self._find_next_deadlined_task(task, category)
            if prerequirement_for:
                task.prerequirement_for_deadlined = prerequirement_for
                task.depends_on_deadlined = self._find_prev_deadlined_task(task, category)
                tasks_preconditioned.append(task)
                # print('{}: {} < {} < {}'.format(category, depends_on, task, prerequirement_for))

        # Second, schedule them
        tasks_preconditioned = self._prioritize_tasks(tasks_preconditioned, stats)
        first_available_date = datetime.combine(date.today(), datetime.min.time())
        remaing_effort = max_load  # assume start at 0 (to avoid modifying schedule during the day)
        for task in tasks_preconditioned:
            after = first_available_date if task.depends_on_deadlined is None else task.depends_on_deadlined.scheduled_end_date(
                category)

            before = None if task.prerequirement_for_deadlined is None else task.prerequirement_for_deadlined.scheduled_start_date(
                category)  # end of day
            new_first_available_date = self._schedule_task_wout_deadline(task, after, max_load, category, tasks, before)
            first_available_date = new_first_available_date if task.depends_on_deadlined is None else first_available_date

        # Step 3: Place all remaining tasks based on priorities
        tasks_wout_deadline = listdiff(tasks_wout_deadline, tasks_preconditioned)
        remaing_effort = max_load  # assume start at 0 (to avoid modifying schedule during the day)
        for task in tasks_wout_deadline:
            first_available_date = self._schedule_task_wout_deadline(
                task, first_available_date, max_load, category, tasks)

    def _find_prev_deadlined_task(self, task, category):
        prev_deadlined_tasks = [t for t in task.section.tasks if t.meta.end_date and t.pos <
                                task.pos and t.has_category(category)]
        if len(prev_deadlined_tasks) > 0:
            return prev_deadlined_tasks[-1]
        else:
            return None

    def _find_next_deadlined_task(self, task, category):
        next_deadlined_tasks = [t for t in task.section.tasks if t.meta.end_date and t.pos >
                                task.pos and t.has_category(category)]
        if len(next_deadlined_tasks) > 0:
            return next_deadlined_tasks[0]
        else:
            return None

    def _compute_schedule(self, sections, statistics):
        sections = [section for section in sections if section.weight > 0]

        nested_tasks = map(lambda section: section.tasks, sections)
        all_tasks = [task for tasks in nested_tasks for task in tasks]

        schedules = []
        for category in statistics.categories:
            category_tasks = [t for t in all_tasks if t.has_category(category)]
            self._compute_schedule_for_category(category_tasks, category, statistics)

    def _fold_links(self):
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

        self.view.unfold(sublime.Region(0, self.view.size()))
        self.view.fold(regions)

    def _mark_date_completed(self, sections, edit):
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
                    # formatted_marker = " %s(%s)" % (DATE_MARKER, date.today())

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

            effort[str(category)] = cat_effort
        return effort

    def _draw_weekly_schedule(self, sections, edit, statistics):

        heading_region = self.view.find('^## Plan: (\d+w? )?Week(.+) effort timeline', 0)
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
        effort_content += "".join(["{:<5}  ".format(cat) for cat in statistics.categories]) + '\n'

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

    def _draw_section_schedule(self, sections, edit, statistics, to_scale=False):

        heading_region = self.view.find('^## Plan: (\d+w )?[Ss]ection schedule', 0)
        if heading_region.begin() == -1:
            return

        line = self.view.line(heading_region)

        match = re.search('(?P<num_weeks>\d+).+', self.view.substr(line))
        for_weeks = int(match.group('num_weeks')) if match else 30
        for_weeks = min(for_weeks, 60)

        match = re.search('.+(?P<to_scale>to scale)\s*', self.view.substr(line))
        to_scale = True if match and match.group('to_scale') else to_scale

        data = []
        smallest = 0
        largest = 40
        for section in sections:
            if section.is_valid:
                weekly_load = self._compute_total_weekly_load(
                    section, statistics, for_weeks=for_weeks, quarter_breaks=self.show_quarters)
                largest = max(largest, max([w for w in weekly_load if w is not None]))
                data.append((weekly_load, section.title[3:]))

        MAX_WIDTH = 76
        title_width = MAX_WIDTH - for_weeks - 1
        title_width -= len([w for w in weekly_load if w is None])

        fmt_string = '{:<' + str(title_width) + '} {}\n'

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

    def add_error(self, category, error):
        exists = [err for err in self.errors if err['category'] == category]

        if exists:
            exists[0]['errors'].append(error)
        else:
            self.errors.append({
                'category': category,
                'errors': [error]
            })

    def _errors_content(self):
        content = ''
        if len(self.errors) > 0:
            content = '\n\nThere are errors in your plan:\n\n'
            for errorgroup in self.errors:
                content += '*{}*:\n'.format(errorgroup['category'])
                for error in errorgroup['errors']:
                    content += '- {}\n'.format(error)
                content += '\n'
        else:
            content = '\n\n'

        return content

    def _update_timestamp_and_errors(self, edit):

        heading_region = self.view.find('## Plan: Information', 0, sublime.LITERAL)

        if heading_region.begin() == -1:
            return

        line = self.view.line(heading_region)

        next_section_index = self.view.find('^##', line.end()).begin()
        replace_region = sublime.Region(line.end(), next_section_index)
        content = 'Last updated: {}'.format(datetime.now().strftime("%Y-%m-%d"))

        content += self._errors_content()

        self.view.replace(edit, replace_region, '\n\n' + content)

    def _show_tooltip(self, sections):
        cursor = self.view.sel()[0].begin()
        line = self.view.line(cursor)

        line_content = self.view.substr(line)

        if line_content.startswith('-'):
            task = None
            for s in sections:
                task = s.find_by_line(line_content)
                if task is not None:
                    break

            if task:
                content = ''
                categories = task.categories()
                max_len = max([len(c) for c in categories])
                data = []
                for cat in categories:
                    data.append((
                        cat,
                        task.scheduled_start_date(cat).date(),
                        task.scheduled_end_date(cat).date()
                    ))

                data = sorted(data, key=itemgetter(1))

                for d in data:
                    if d[1] == d[2]:
                        content += '{:.>{}}: {}\n\n'.format(
                            d[0],
                            max_len,
                            d[1])
                    else:
                        content += '{:.>{}}: {} - {}\n\n'.format(
                            d[0],
                            max_len,
                            d[1],
                            d[2])
                mdpopups.show_popup(self.view, content)

    def run(self, edit):

        self.errors = []
        self.myrandomseed = 4567
        conf = sublime.load_settings('ProjectPlanner.sublime-settings')
        self.show_quarters = conf.get('show_quarters_on_graphs')

        content = self.view.substr(sublime.Region(0, self.view.size()))
        sections = self._extract_sections(content)

        statistics = self._compute_statistics(sections)
        self._estimate_missing_data(sections, statistics)
        self._compute_schedule(sections, statistics)

        self._mark_date_completed(sections, edit)
        self._update_section_timings(sections, edit, statistics)
        self._update_upcoming_tasks(sections, edit, statistics)
        self._update_planned_effort(sections, edit, statistics)
        self._draw_weekly_schedule(sections, edit, statistics)
        self._draw_section_schedule(sections, edit, statistics)
        self._update_timestamp_and_errors(edit)

        self._fold_links()

        self._show_tooltip(sections)

        # This is used by ProjectPlannerTimelineView
        self.sections = sections
