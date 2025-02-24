from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import pandas as pd
from datetime import datetime, date, time, timedelta
import os

app = Flask(__name__)
app.secret_key = "replace-with-a-secret-key"

# Global variables to store student data, schedule, and machine-level pairs
student_data = pd.DataFrame()
# Pre-populate some machine-level pairs for testing (optional)
machine_levels = [
    {"id": 1, "machine": "Lathe", "level": "High"},
    {"id": 2, "machine": "Milling", "level": "Medium"},
    {"id": 3, "machine": "Drill", "level": "Low"}
]
schedule_df = pd.DataFrame()

def get_next_machine_id():
    if machine_levels:
        return max(entry["id"] for entry in machine_levels) + 1
    return 1

@app.route('/')
def index():
    schedule_form = session.get('schedule_form', {})
    students = []
    if not student_data.empty:
        students = student_data.to_dict(orient='records')
    # Pass the schedule DataFrame as well; it will be converted to dict in the template.
    return render_template('index.html', 
                           schedule_df=schedule_df, 
                           machine_levels=machine_levels, 
                           schedule_form=schedule_form,
                           students=students)


# Route for uploading student data
@app.route('/upload', methods=['POST'])
def upload_file():
    global student_data
    if 'file' in request.files:
        file = request.files['file']
        try:
            student_data = pd.read_excel(file)
            flash("Student data uploaded successfully.", "success")
        except Exception as e:
            flash(f"Error reading file: {e}", "error")
    return redirect(url_for('index'))

# Route for adding machines and levels
@app.route('/add_machine_level', methods=['POST'])
def add_machine_level():
    machine_input = request.form.get('machine')
    level_input = request.form.get('level')
    if machine_input and level_input:
        new_id = get_next_machine_id()
        machine_levels.append({"id": new_id, "machine": machine_input, "level": level_input})
        flash(f"Machine '{machine_input}' with Level '{level_input}' added successfully.", "success")
    else:
        flash("Please provide both machine and level.", "error")
    return redirect(url_for('index'))

# Route for updating a machine (inline editing)
@app.route('/update_machine', methods=['POST'])
def update_machine():
    data = request.get_json()
    machine_id = int(data.get('id'))
    new_name = data.get('name')
    new_level = data.get('level')
    for entry in machine_levels:
        if entry["id"] == machine_id:
            entry["machine"] = new_name
            entry["level"] = new_level
            return jsonify({'success': True})
    return jsonify({'success': False})

# Route for deleting a machine
@app.route('/delete_machine', methods=['POST'])
def delete_machine():
    data = request.get_json()
    machine_id = int(data.get('id'))
    global machine_levels
    machine_levels = [entry for entry in machine_levels if entry["id"] != machine_id]
    return jsonify({'success': True})

# Route for generating the schedule (multi-day scheduling with allowance)
@app.route('/generate_schedule', methods=['POST'])
def generate_schedule():
    global schedule_df
    try:
        # Store form data in session for persistence.
        session['schedule_form'] = {
            'slot_duration': request.form['slot_duration'],
            'start_date': request.form['start_date'],
            'end_date': request.form['end_date'],
            'start_time': request.form['start_time'],
            'end_time': request.form['end_time'],
            'threshold_mark': request.form['threshold_mark'],
            'auto_extra_time': request.form['auto_extra_time'],
            'scheduling_mode': request.form.get('scheduling_mode', 'forward'),
            'priority_rule': request.form.get('priority_rule', 'FIFO'),
            'allowance_time': request.form.get('allowance_time', '0')
        }
        
        base_slot_duration = int(request.form['slot_duration'])
        # New fields: start_date and end_date (YYYY-MM-DD)
        start_date_str = request.form['start_date']
        end_date_str = request.form['end_date']
        # Working day times
        start_time_str = request.form['start_time']
        end_time_str = request.form['end_time']
        
        # Convert to date and time objects
        start_date_obj = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date_obj = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        start_time_obj = datetime.strptime(start_time_str, "%H:%M").time()
        end_time_obj = datetime.strptime(end_time_str, "%H:%M").time()
        
        # Combine to create datetime objects for first working period and final limit.
        current_dt = datetime.combine(start_date_obj, start_time_obj)
        final_dt = datetime.combine(end_date_obj, end_time_obj)
        
        allowance_time = int(request.form.get('allowance_time', 0))
        threshold_mark = float(request.form.get('threshold_mark', 0))
        auto_extra_time = int(request.form.get('auto_extra_time', 0))
        
        scheduling_mode = request.form.get('scheduling_mode', 'forward')  # forward/backward
        priority_rule = request.form.get('priority_rule', 'FIFO')  # FIFO, SPT, LPT
        
        # Prepare orders from student_data
        orders = []
        for idx, row in student_data.iterrows():
            student_name = row["Student Name"]
            student_level = row["Level"]
            student_mark = float(row["Mark"])
            extra_time = auto_extra_time if student_mark < threshold_mark else 0
            processing_time = base_slot_duration + extra_time
            orders.append({
                "Student": student_name,
                "Level": student_level,
                "Mark": student_mark,
                "processing_time": processing_time,
                "extra_time": extra_time
            })
        
        if priority_rule == 'SPT':
            orders = sorted(orders, key=lambda x: x["processing_time"])
        elif priority_rule == 'LPT':
            orders = sorted(orders, key=lambda x: x["processing_time"], reverse=True)
        # FIFO keeps original order
        
        schedule = []
        if scheduling_mode == 'forward':
            # Continue scheduling while current_dt is before final_dt and orders remain.
            for order in orders:
                # If current_dt has passed the current day’s working end, jump to next day’s start.
                working_day_end = datetime.combine(current_dt.date(), end_time_obj)
                if current_dt >= working_day_end:
                    next_day = current_dt.date() + timedelta(days=1)
                    # If we exceed the final day, break out.
                    if next_day > end_date_obj:
                        break
                    current_dt = datetime.combine(next_day, start_time_obj)
                    working_day_end = datetime.combine(current_dt.date(), end_time_obj)
                
                slot_duration = order["processing_time"]
                slot_end_dt = current_dt + timedelta(minutes=slot_duration)
                # If the slot would extend past today's working hours, then move to the next day.
                if slot_end_dt > working_day_end:
                    next_day = current_dt.date() + timedelta(days=1)
                    if next_day > end_date_obj:
                        break
                    current_dt = datetime.combine(next_day, start_time_obj)
                    slot_end_dt = current_dt + timedelta(minutes=slot_duration)
                    working_day_end = datetime.combine(current_dt.date(), end_time_obj)
                
                # Append schedule record with full date and time.
                order.update({
                    "Machine": machine_levels[len(schedule) % len(machine_levels)]["machine"] if machine_levels else "N/A",
                    "Start Time": current_dt.strftime("%Y-%m-%d %H:%M"),
                    "End Time": slot_end_dt.strftime("%Y-%m-%d %H:%M")
                })
                schedule.append(order)
                
                # Increment current_dt by slot duration plus allowance time.
                current_dt = slot_end_dt + timedelta(minutes=allowance_time)
                # If current_dt exceeds working_day_end, roll over to next day.
                if current_dt > working_day_end:
                    next_day = current_dt.date() + timedelta(days=1)
                    if next_day > end_date_obj:
                        break
                    current_dt = datetime.combine(next_day, start_time_obj)
        else:
            # Backward scheduling: similar logic in reverse.
            current_dt = datetime.combine(end_date_obj, end_time_obj)
            backward_schedule = []
            for order in orders:
                working_day_start = datetime.combine(current_dt.date(), start_time_obj)
                slot_duration = order["processing_time"]
                slot_start_dt = current_dt - timedelta(minutes=slot_duration)
                if slot_start_dt < working_day_start:
                    prev_day = current_dt.date() - timedelta(days=1)
                    if prev_day < start_date_obj:
                        break
                    current_dt = datetime.combine(prev_day, end_time_obj)
                    working_day_start = datetime.combine(current_dt.date(), start_time_obj)
                    slot_start_dt = current_dt - timedelta(minutes=slot_duration)
                order.update({
                    "Machine": machine_levels[len(backward_schedule) % len(machine_levels)]["machine"] if machine_levels else "N/A",
                    "Start Time": slot_start_dt.strftime("%Y-%m-%d %H:%M"),
                    "End Time": current_dt.strftime("%Y-%m-%d %H:%M")
                })
                backward_schedule.append(order)
                # Subtract allowance time and slot duration for next order.
                current_dt = slot_start_dt - timedelta(minutes=allowance_time)
            schedule = backward_schedule[::-1]
        
        schedule_df = pd.DataFrame(schedule)
        flash("Schedule generated successfully over multiple days.", "success")
        return redirect(url_for('index'))
        
    except Exception as e:
        flash(f"Error generating schedule: {e}", "error")
        return redirect(url_for('index'))

# Route for viewing the generated schedule (updated to display full date & time)
@app.route('/view_schedule')
def view_schedule():
    global schedule_df
    if schedule_df.empty:
        flash("No schedule generated yet.", "error")
        return redirect(url_for('index'))
    schedule_data = schedule_df.to_dict(orient='records')
    schedule_html = schedule_df.to_html(classes='data-table', index=False)
    return render_template('view_schedule.html', schedule_data=schedule_data, schedule_html=schedule_html)

# Route for manual time adjustment (unchanged)
@app.route('/manual_update', methods=['POST'])
def manual_update():
    global schedule_df
    try:
        student_index = int(request.form['student_index'])
        manual_extra_time = int(request.form['manual_extra_time'])
        
        if schedule_df.empty:
            flash("Please generate a schedule first.", "error")
            return redirect(url_for('index'))
        if student_index < 0 or student_index >= len(schedule_df):
            flash("Invalid student index.", "error")
            return redirect(url_for('index'))
        
        schedule_df.at[schedule_df.index[student_index], "extra_time"] += manual_extra_time
        
        base_slot_duration = int(request.form.get('slot_duration_manual', 0))
        if base_slot_duration <= 0:
            flash("Please provide a valid base slot duration for recalculation.", "error")
            return redirect(url_for('index'))
        
        # This manual update route should also be adapted for multi-day schedules.
        # For simplicity, we assume similar logic as forward scheduling.
        if student_index == 0:
            current_dt = datetime.strptime(request.form.get('start_time_manual'), "%Y-%m-%d %H:%M")
        else:
            prev_end = schedule_df.at[schedule_df.index[student_index - 1], "End Time"]
            current_dt = datetime.strptime(prev_end, "%Y-%m-%d %H:%M")
        
        for idx in range(student_index, len(schedule_df)):
            extra_time = schedule_df.at[schedule_df.index[idx], "extra_time"]
            total_duration = base_slot_duration + extra_time
            new_end_dt = current_dt + timedelta(minutes=total_duration)
            schedule_df.at[schedule_df.index[idx], "Start Time"] = current_dt.strftime("%Y-%m-%d %H:%M")
            schedule_df.at[schedule_df.index[idx], "End Time"] = new_end_dt.strftime("%Y-%m-%d %H:%M")
            current_dt = new_end_dt
            
        flash("Manual adjustment applied successfully.", "success")
        return redirect(url_for('index'))
    
    except Exception as e:
        flash(f"Error in manual update: {e}", "error")
        return redirect(url_for('index'))

# Route for exporting the schedule
@app.route('/export_schedule')
def export_schedule():
    if not schedule_df.empty:
        file_path = os.path.join('static', 'schedule.xlsx')
        try:
            schedule_df.to_excel(file_path, index=False)
            flash("Schedule exported successfully.", "success")
            return redirect(url_for('static', filename='schedule.xlsx'))
        except Exception as e:
            flash(f"Error exporting schedule: {e}", "error")
            return redirect(url_for('index'))
    else:
        flash("No schedule to export. Please generate a schedule first.", "error")
        return redirect(url_for('index'))

# Route for viewing uploaded student data
@app.route('/view_data')
def view_data():
    global student_data
    if student_data.empty:
        flash("No data uploaded yet.", "error")
        return redirect(url_for('index'))
    data_html = student_data.to_html(classes='data-table', index=False)
    return render_template('view_data.html', data_html=data_html)

if __name__ == '__main__':
    app.run(debug=True)
