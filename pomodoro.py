#!/usr/bin/env python3
"""
Pomodoro Timer - A terminal-based productivity timer
Usage: pomodoro <task_name> <duration>  (e.g., pomodoro learning_option_trading 1h)
       pomodoro stats  (opens web interface with statistics)
"""

import time
import sys
import os
import argparse
import re
import sqlite3
import json
import subprocess
import socket
from datetime import datetime, timedelta
from pathlib import Path

# Database setup
DB_PATH = Path.home() / ".pomodoro" / "sessions.db"
DB_PATH.parent.mkdir(exist_ok=True)

# Task colors setup
COLORS_PATH = Path.home() / ".pomodoro" / "task_colors.json"

# CalDAV config setup
CALDAV_CONFIG_PATH = Path.home() / ".pomodoro" / "caldav_config.json"

class TaskColorManager:
    """Manages color assignments for tasks"""
    
    def __init__(self, colors_path=COLORS_PATH):
        self.colors_path = colors_path
        self.colors = self.load_colors()
    
    def load_colors(self):
        """Load task colors from JSON file"""
        if self.colors_path.exists():
            try:
                with open(self.colors_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}
    
    def save_colors(self):
        """Save task colors to JSON file"""
        try:
            with open(self.colors_path, 'w') as f:
                json.dump(self.colors, f, indent=2)
        except IOError:
            pass
    
    def generate_color(self, task_name):
        """Generate a unique color for a task using hash function"""
        # Use hash to generate consistent colors
        hash_value = hash(task_name)
        
        # Generate HSL color with good saturation and lightness
        # Use golden ratio for better color distribution
        hue = (hash_value % 360)
        saturation = 60 + (hash_value % 30)  # 60-90%
        lightness = 45 + (hash_value % 20)    # 45-65%
        
        # Convert HSL to RGB
        h = hue / 360.0
        s = saturation / 100.0
        l = lightness / 100.0
        
        if s == 0:
            r = g = b = l
        else:
            def hue_to_rgb(p, q, t):
                if t < 0: t += 1
                if t > 1: t -= 1
                if t < 1/6: return p + (q - p) * 6 * t
                if t < 1/2: return q
                if t < 2/3: return p + (q - p) * (2/3 - t) * 6
                return p
            
            q = l * (1 + s) if l < 0.5 else l + s - l * s
            p = 2 * l - q
            r = hue_to_rgb(p, q, h + 1/3)
            g = hue_to_rgb(p, q, h)
            b = hue_to_rgb(p, q, h - 1/3)
        
        # Convert to hex
        r = int(round(r * 255))
        g = int(round(g * 255))
        b = int(round(b * 255))
        
        return f"#{r:02x}{g:02x}{b:02x}"
    
    def get_color(self, task_name):
        """Get color for a task, generating if not exists"""
        if task_name not in self.colors:
            self.colors[task_name] = self.generate_color(task_name)
            self.save_colors()
        return self.colors[task_name]
    
    def set_color(self, task_name, color):
        """Set a custom color for a task"""
        self.colors[task_name] = color
        self.save_colors()
    
    def get_all_colors(self):
        """Get all task colors"""
        return self.colors.copy()

class PomodoroDatabase:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """Initialize the database with sessions table"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                status TEXT NOT NULL,
                completed_seconds INTEGER DEFAULT 0
            )
        ''')
        # Table to track sync state between sessions and calendar events
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sync_mapping (
                session_id INTEGER PRIMARY KEY,
                calendar_uid TEXT UNIQUE NOT NULL,
                calendar_url TEXT,
                last_synced TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        ''')
        conn.commit()
        conn.close()
    
    def save_session(self, task_name, duration_seconds, start_time, end_time, status, completed_seconds=0):
        """Save a work session to the database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sessions (task_name, duration_seconds, start_time, end_time, status, completed_seconds)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (task_name, duration_seconds, start_time.isoformat(), end_time.isoformat() if end_time else None, status, completed_seconds))
        conn.commit()
        conn.close()
        print(f"💾 Session saved: {task_name} - {status} - {completed_seconds}s completed")
    
    def get_all_sessions(self):
        """Get all sessions from the database"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM sessions ORDER BY start_time DESC')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def get_stats(self, period='week'):
        """Get statistics about sessions for a specific period"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Calculate date range based on period
        from datetime import datetime, timedelta
        today = datetime.now().date()
        
        if period == 'week':
            start_date = today - timedelta(days=7)
        elif period == 'month':
            start_date = today - timedelta(days=30)
        elif period == 'year':
            start_date = today - timedelta(days=365)
        else:  # 'all'
            start_date = None
        
        # Build WHERE clause
        if start_date:
            date_filter = f"WHERE DATE(start_time) >= '{start_date.isoformat()}'"
        else:
            date_filter = ""
        
        # Total sessions for period
        cursor.execute(f'SELECT COUNT(*) FROM sessions {date_filter}')
        total_sessions = cursor.fetchone()[0]
        
        # Total time for period
        cursor.execute(f'SELECT SUM(completed_seconds) FROM sessions {date_filter}')
        total_time_all = cursor.fetchone()[0] or 0
        
        # Sessions by task for period
        cursor.execute(f'''
            SELECT task_name, COUNT(*) as count, SUM(completed_seconds) as total_time
            FROM sessions
            {date_filter}
            GROUP BY task_name
            ORDER BY total_time DESC
        ''')
        by_task = cursor.fetchall()
        
        # Sessions by date for period
        cursor.execute(f'''
            SELECT DATE(start_time) as date, COUNT(*) as count, SUM(completed_seconds) as total_time
            FROM sessions
            {date_filter}
            GROUP BY DATE(start_time)
            ORDER BY date DESC
        ''')
        by_date = cursor.fetchall()
        
        conn.close()
        
        return {
            'period': period,
            'total_sessions': total_sessions,
            'total_time_all': total_time_all,
            'by_task': [{'task_name': row[0], 'count': row[1], 'total_time': row[2]} for row in by_task],
            'by_date': [{'date': row[0], 'count': row[1], 'total_time': row[2]} for row in by_date]
        }
    
    def update_session(self, session_id, task_name=None, duration_seconds=None, start_time=None, end_time=None, status=None, completed_seconds=None):
        """Update a session in the database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        updates = []
        params = []
        
        if task_name is not None:
            updates.append('task_name = ?')
            params.append(task_name)
        if duration_seconds is not None:
            updates.append('duration_seconds = ?')
            params.append(duration_seconds)
        if start_time is not None:
            updates.append('start_time = ?')
            params.append(start_time.isoformat() if hasattr(start_time, 'isoformat') else start_time)
        if end_time is not None:
            updates.append('end_time = ?')
            params.append(end_time.isoformat() if hasattr(end_time, 'isoformat') else end_time if end_time else None)
        if status is not None:
            updates.append('status = ?')
            params.append(status)
        if completed_seconds is not None:
            updates.append('completed_seconds = ?')
            params.append(completed_seconds)
        
        if updates:
            params.append(session_id)
            cursor.execute(f'''
                UPDATE sessions
                SET {', '.join(updates)}
                WHERE id = ?
            ''', params)
            conn.commit()
        
        conn.close()
        return True
    
    def session_exists(self, session_id):
        """Check if a session exists"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM sessions WHERE id = ?', (session_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    
    def find_session_by_time_and_task(self, start_time, task_name, tolerance_minutes=5):
        """Find an existing session by start time and task name (within tolerance)"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Convert start_time to string if it's a datetime
        if hasattr(start_time, 'isoformat'):
            start_time_str = start_time.isoformat()
        else:
            start_time_str = start_time
        
        # Find sessions with same task name and start time within tolerance
        tolerance_seconds = tolerance_minutes * 60
        cursor.execute('''
            SELECT * FROM sessions 
            WHERE task_name = ? 
            AND ABS(CAST((julianday(start_time) - julianday(?)) * 86400 AS INTEGER)) <= ?
            ORDER BY ABS(CAST((julianday(start_time) - julianday(?)) * 86400 AS INTEGER)) ASC
            LIMIT 1
        ''', (task_name, start_time_str, tolerance_seconds, start_time_str))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return dict(row)
        return None
    
    def delete_session(self, session_id):
        """Delete a session from the database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
        conn.commit()
        conn.close()
        return True
    
    def add_session(self, task_name, duration_seconds, start_time, end_time=None, status='completed', completed_seconds=0):
        """Add a new session to the database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sessions (task_name, duration_seconds, start_time, end_time, status, completed_seconds)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            task_name,
            duration_seconds,
            start_time.isoformat() if hasattr(start_time, 'isoformat') else start_time,
            end_time.isoformat() if end_time and hasattr(end_time, 'isoformat') else (end_time if end_time else None),
            status,
            completed_seconds
        ))
        conn.commit()
        session_id = cursor.lastrowid
        conn.close()
        return session_id
    
    def get_sync_mapping(self, session_id=None, calendar_uid=None):
        """Get sync mapping by session_id or calendar_uid"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if session_id:
            cursor.execute('SELECT * FROM sync_mapping WHERE session_id = ?', (session_id,))
        elif calendar_uid:
            cursor.execute('SELECT * FROM sync_mapping WHERE calendar_uid = ?', (calendar_uid,))
        else:
            cursor.execute('SELECT * FROM sync_mapping')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def set_sync_mapping(self, session_id, calendar_uid, calendar_url=None):
        """Set sync mapping between session and calendar event"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO sync_mapping (session_id, calendar_uid, calendar_url, last_synced)
            VALUES (?, ?, ?, ?)
        ''', (session_id, calendar_uid, calendar_url, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    def delete_sync_mapping(self, session_id=None, calendar_uid=None):
        """Delete sync mapping"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        if session_id:
            cursor.execute('DELETE FROM sync_mapping WHERE session_id = ?', (session_id,))
        elif calendar_uid:
            cursor.execute('DELETE FROM sync_mapping WHERE calendar_uid = ?', (calendar_uid,))
        conn.commit()
        conn.close()

class CalDAVSync:
    """Handles bidirectional sync with CalDAV calendars (iCloud, Google Calendar, etc.)"""
    
    def __init__(self, db, config_path=CALDAV_CONFIG_PATH):
        self.db = db
        self.config_path = config_path
        self.config = self.load_config()
        self.client = None
        self.calendar = None
    
    def load_config(self):
        """Load CalDAV configuration"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}
    
    def save_config(self):
        """Save CalDAV configuration"""
        try:
            self.config_path.parent.mkdir(exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(self.config, f, indent=2)
        except IOError:
            pass
    
    def configure(self, url, username, password, calendar_name=None):
        """Configure CalDAV connection. Returns (success: bool, error: str or None)"""
        self.config = {
            'url': url,
            'username': username,
            'password': password,
            'calendar_name': calendar_name or 'Pomodoro Sessions'
        }
        self.save_config()
        return self.connect()
    
    def connect(self):
        """Connect to CalDAV server. Returns (success: bool, error: str or None)"""
        if not self.config.get('url'):
            return False, 'No CalDAV URL configured'
        
        try:
            try:
                import caldav
                from caldav import DAVClient
            except ImportError:
                return False, "caldav package not installed. Install with: pip install caldav"
            
            self.client = DAVClient(
                url=self.config['url'],
                username=self.config['username'],
                password=self.config['password']
            )
            
            # Find or create calendar
            principal = self.client.principal()
            calendars = principal.calendars()
            
            calendar_name = self.config.get('calendar_name', 'Pomodoro Sessions')
            self.calendar = None
            
            for cal in calendars:
                if cal.name == calendar_name:
                    self.calendar = cal
                    break
            
            if not self.calendar:
                # Create calendar if it doesn't exist
                self.calendar = principal.make_calendar(name=calendar_name)
            
            return True, None
        except ImportError as e:
            error_msg = f"CalDAV import error: {e}"
            print(error_msg)
            return False, str(e)
        except Exception as e:
            error_msg = f"CalDAV connection error: {e}"
            print(error_msg)
            return False, str(e)
    
    def sync_to_calendar(self):
        """Sync local sessions to calendar"""
        if not self.calendar:
            success, error = self.connect()
            if not success:
                return {'success': False, 'error': error or 'Not connected to CalDAV server'}
        
        try:
            sessions = self.db.get_all_sessions()
            synced = 0
            updated = 0
            deleted = 0
            
            # Get all sync mappings to find orphaned ones (sessions that were deleted)
            all_mappings = self.db.get_sync_mapping()
            session_ids = {s['id'] for s in sessions}
            
            # Build a set of calendar UIDs that should exist (from current sessions)
            expected_uids = set()
            for session in sessions:
                uid = f"pomodoro-{session['id']}@pomodoro-timer"
                expected_uids.add(uid)
            
            # Also check mappings for sessions that still exist
            for mapping in all_mappings:
                if mapping['session_id'] in session_ids:
                    expected_uids.add(mapping['calendar_uid'])
            
            # Find calendar events that correspond to deleted sessions
            # First, check mappings for deleted sessions
            for mapping in all_mappings:
                session_id = mapping['session_id']
                calendar_uid = mapping['calendar_uid']
                
                # If the session no longer exists locally, delete it from calendar
                if session_id not in session_ids:
                    try:
                        events = self.calendar.search(uid=calendar_uid)
                        if events:
                            event = events[0]
                            # Reload the event to get fresh ETag before deleting
                            try:
                                event.load()
                            except:
                                pass  # If load fails, try delete anyway
                            try:
                                event.delete()
                                deleted += 1
                            except Exception as delete_error:
                                # Check if it's a 412 Precondition Failed - common and recoverable
                                error_str = str(delete_error)
                                is_412_error = '412' in error_str or 'Precondition Failed' in error_str
                                
                                # If delete fails (e.g., 412 error), try to get the event URL and delete directly
                                try:
                                    event_url = str(event.url)
                                    # Try deleting by URL
                                    self.calendar.client.delete(event_url)
                                    deleted += 1
                                except Exception as direct_delete_error:
                                    # Only log if both methods failed and it's not a 412 error
                                    if not is_412_error:
                                        print(f"Error deleting calendar event {calendar_uid}: {delete_error}, direct delete also failed: {direct_delete_error}")
                        # Clean up the orphaned mapping
                        self.db.delete_sync_mapping(calendar_uid=calendar_uid)
                    except Exception as e:
                        print(f"Error deleting calendar event {calendar_uid}: {e}")
                        # Still clean up the mapping even if deletion failed
                        self.db.delete_sync_mapping(calendar_uid=calendar_uid)
            
            # Also search calendar for any pomodoro events that don't have mappings
            # This catches cases where mappings were deleted but events remain
            try:
                # Search for all pomodoro events in the calendar
                start_date = datetime.now() - timedelta(days=365)
                end_date = datetime.now() + timedelta(days=365)
                all_calendar_events = list(self.calendar.search(
                    start=start_date,
                    end=end_date
                ))
                
                print(f"Found {len(all_calendar_events)} total events in calendar to check for deletion")
                
                for event in all_calendar_events:
                    try:
                        vevent = event.icalendar_component
                        uid = str(vevent.get('uid', ''))
                        summary = str(vevent.get('summary', ''))
                        
                        # Check if it's a pomodoro event
                        is_pomodoro = (uid.startswith('pomodoro-') and '@pomodoro-timer' in uid) or \
                                     ('🍅' in summary or 'pomodoro' in summary.lower())
                        
                        if is_pomodoro:
                            # Check if this event should exist (has a corresponding session or mapping)
                            if uid not in expected_uids:
                                # Check if there's a mapping for this UID
                                mapping = self.db.get_sync_mapping(calendar_uid=uid)
                                if not mapping or (mapping and mapping[0]['session_id'] not in session_ids):
                                    # Event exists but session doesn't - delete it
                                    try:
                                        # Reload the event to get fresh ETag before deleting
                                        try:
                                            event.load()
                                        except:
                                            pass  # If load fails, try delete anyway
                                        try:
                                            event.delete()
                                            deleted += 1
                                        except Exception as delete_error:
                                            # Check if it's a 412 Precondition Failed - common and recoverable
                                            error_str = str(delete_error)
                                            is_412_error = '412' in error_str or 'Precondition Failed' in error_str
                                            
                                            # If delete fails (e.g., 412 error), try to get the event URL and delete directly
                                            try:
                                                event_url = str(event.url)
                                                # Try deleting by URL
                                                self.calendar.client.delete(event_url)
                                                deleted += 1
                                            except Exception as direct_delete_error:
                                                # Only log if both methods failed and it's not a 412 error
                                                if not is_412_error:
                                                    print(f"Error deleting calendar event {uid}: {delete_error}, direct delete also failed: {direct_delete_error}")
                                        # Clean up mapping if it exists
                                        if mapping:
                                            self.db.delete_sync_mapping(calendar_uid=uid)
                                    except Exception as e:
                                        print(f"Error deleting orphaned calendar event {uid}: {e}")
                    except Exception as e:
                        print(f"Error checking calendar event: {e}")
                        continue
            except Exception as e:
                print(f"Error searching calendar for orphaned events: {e}")
            
            # Now sync existing sessions
            for session in sessions:
                # Check if already synced
                mapping = self.db.get_sync_mapping(session_id=session['id'])
                
                start_time = datetime.fromisoformat(session['start_time'].replace('Z', '+00:00'))
                end_time = session['end_time']
                if end_time:
                    end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                else:
                    end_time = start_time + timedelta(seconds=session['duration_seconds'])
                
                uid = f"pomodoro-{session['id']}@pomodoro-timer"
                summary = f"🍅 {session['task_name']}"
                description = f"Pomodoro session: {session['task_name']}\nDuration: {session['duration_seconds']//60} minutes\nCompleted: {session['completed_seconds']//60} minutes\nStatus: {session['status']}"
                
                if mapping:
                    # Update existing event using the mapped UID
                    calendar_uid = mapping[0]['calendar_uid']
                    try:
                        events = self.calendar.search(uid=calendar_uid)
                        if events:
                            event = events[0]
                            # Reload event to get fresh ETag before updating
                            try:
                                event.load()
                            except:
                                pass  # If load fails, try update anyway
                            
                            try:
                                event.icalendar_component['summary'] = summary
                                event.icalendar_component['description'] = description
                                event.icalendar_component['dtstart'].dt = start_time
                                event.icalendar_component['dtend'].dt = end_time
                                event.save()
                                updated += 1
                            except Exception as save_error:
                                # Check if it's a 412 Precondition Failed (ETag mismatch) - common and recoverable
                                error_str = str(save_error)
                                is_412_error = '412' in error_str or 'Precondition Failed' in error_str
                                
                                # If save fails (e.g., 412 error), try recreating the event
                                if not is_412_error:
                                    print(f"Error updating calendar event {calendar_uid}: {save_error}, recreating...")
                                try:
                                    # Delete old event first
                                    try:
                                        event.delete()
                                    except:
                                        pass
                                    # Create new event with same UID
                                    from icalendar import Calendar, Event
                                    cal = Calendar()
                                    cal.add('prodid', '-//Pomodoro Timer//EN')
                                    cal.add('version', '2.0')
                                    
                                    new_event = Event()
                                    new_event.add('summary', summary)
                                    new_event.add('description', description)
                                    new_event.add('dtstart', start_time)
                                    new_event.add('dtend', end_time)
                                    new_event.add('uid', calendar_uid)
                                    cal.add_component(new_event)
                                    
                                    self.calendar.add_event(cal.to_ical())
                                    updated += 1
                                except Exception as recreate_error:
                                    print(f"Error recreating calendar event {calendar_uid}: {recreate_error}")
                                    # Fall through to create new event below
                                    mapping = []
                        else:
                            # Event was deleted from calendar, recreate it with same UID
                            from icalendar import Calendar, Event
                            cal = Calendar()
                            cal.add('prodid', '-//Pomodoro Timer//EN')
                            cal.add('version', '2.0')
                            
                            event = Event()
                            event.add('summary', summary)
                            event.add('description', description)
                            event.add('dtstart', start_time)
                            event.add('dtend', end_time)
                            event.add('uid', calendar_uid)  # Use existing UID from mapping
                            cal.add_component(event)
                            
                            self.calendar.add_event(cal.to_ical())
                            synced += 1
                    except Exception as e:
                        # Check if it's a 412 Precondition Failed - common and recoverable
                        error_str = str(e)
                        is_412_error = '412' in error_str or 'Precondition Failed' in error_str
                        if not is_412_error:
                            print(f"Error updating calendar event: {e}")
                        # Event not found, recreate with same UID
                        from icalendar import Calendar, Event
                        cal = Calendar()
                        cal.add('prodid', '-//Pomodoro Timer//EN')
                        cal.add('version', '2.0')
                        
                        event = Event()
                        event.add('summary', summary)
                        event.add('description', description)
                        event.add('dtstart', start_time)
                        event.add('dtend', end_time)
                        event.add('uid', calendar_uid)  # Use existing UID from mapping
                        cal.add_component(event)
                        
                        self.calendar.add_event(cal.to_ical())
                        synced += 1
                else:
                    # No mapping exists - create new event and mapping
                    from icalendar import Calendar, Event
                    cal = Calendar()
                    cal.add('prodid', '-//Pomodoro Timer//EN')
                    cal.add('version', '2.0')
                    
                    event = Event()
                    event.add('summary', summary)
                    event.add('description', description)
                    event.add('dtstart', start_time)
                    event.add('dtend', end_time)
                    event.add('uid', uid)
                    cal.add_component(event)
                    
                    self.calendar.add_event(cal.to_ical())
                    self.db.set_sync_mapping(session['id'], uid)
                    synced += 1
            
            result_msg = f"Synced {synced} new, updated {updated} existing"
            if deleted > 0:
                result_msg += f", deleted {deleted} removed events"
            elif len(sessions) == 0 and len(all_mappings) > 0:
                # If no sessions but mappings exist, we should have deleted something
                result_msg += f" (checked {len(all_calendar_events) if 'all_calendar_events' in locals() else 0} calendar events for deletion)"
            
            return {
                'success': True, 
                'synced': synced, 
                'updated': updated,
                'deleted': deleted,
                'message': result_msg
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def sync_from_calendar(self):
        """Sync calendar events to local sessions"""
        if not self.calendar:
            success, error = self.connect()
            if not success:
                return {'success': False, 'error': error or 'Not connected to CalDAV server'}
        
        try:
            # Get events from calendar
            start_date = datetime.now() - timedelta(days=365)
            end_date = datetime.now() + timedelta(days=365)
            events = self.calendar.search(
                start=start_date,
                end=end_date
            )
            
            imported = 0
            updated = 0
            skipped = 0
            total_events = 0
            
            # Convert to list to get count
            events_list = list(events)
            total_events = len(events_list)
            
            for event in events_list:
                try:
                    vevent = event.icalendar_component
                    uid = str(vevent.get('uid', ''))
                    summary = str(vevent.get('summary', ''))
                    dtstart = vevent.get('dtstart')
                    dtend = vevent.get('dtend')
                    
                    # Skip if not a pomodoro event (but also import events that might be manually created)
                    # We'll check if it's a pomodoro event, or if it's a new event we should import
                    is_pomodoro_event = uid.startswith('pomodoro-') and '@pomodoro-timer' in uid
                    
                    # If it's not a pomodoro event, check if we should import it anyway
                    # (for events manually created in calendar)
                    if not is_pomodoro_event:
                        # Check if summary contains pomodoro emoji or keywords
                        summary_lower = summary.lower()
                        if '🍅' not in summary and 'pomodoro' not in summary_lower:
                            skipped += 1
                            continue
                        # Generate a UID for manually created events
                        if not uid or uid == '':
                            task_name_temp = summary.replace('🍅', '').strip() or 'Imported Session'
                            uid = f"pomodoro-manual-{hash(str(dtstart.dt) + task_name_temp)}@pomodoro-timer"
                    
                    task_name = summary.replace('🍅', '').strip()
                    if not task_name:
                        task_name = 'Imported Session'
                    
                    if dtstart:
                        start_time = dtstart.dt
                        if isinstance(start_time, str):
                            start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                        
                        if dtend:
                            end_time = dtend.dt
                            if isinstance(end_time, str):
                                end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                        else:
                            end_time = start_time + timedelta(minutes=25)
                        
                        duration_seconds = int((end_time - start_time).total_seconds())
                        
                        # Check if already synced
                        mapping = self.db.get_sync_mapping(calendar_uid=uid)
                        
                        if mapping:
                            # Check if the session actually exists
                            session_id = mapping[0]['session_id']
                            if self.db.session_exists(session_id):
                                # Update existing session
                                self.db.update_session(
                                    session_id,
                                    task_name=task_name,
                                    start_time=start_time,
                                    end_time=end_time,
                                    duration_seconds=duration_seconds
                                )
                                updated += 1
                            else:
                                # Session was deleted but mapping wasn't cleaned up
                                # Create new session and update mapping
                                self.db.delete_sync_mapping(calendar_uid=uid)
                                session_id = self.db.add_session(
                                    task_name=task_name,
                                    duration_seconds=duration_seconds,
                                    start_time=start_time,
                                    end_time=end_time,
                                    status='completed',
                                    completed_seconds=duration_seconds
                                )
                                self.db.set_sync_mapping(session_id, uid, str(event.url))
                                imported += 1
                        else:
                            # No mapping exists - check if a similar session already exists locally
                            # (maybe it was created manually or from a previous sync)
                            existing_session = self.db.find_session_by_time_and_task(
                                start_time, task_name, tolerance_minutes=5
                            )
                            
                            if existing_session:
                                # Session exists locally but wasn't synced - create mapping
                                session_id = existing_session['id']
                                # Check if this session already has a mapping
                                existing_mapping = self.db.get_sync_mapping(session_id=session_id)
                                if not existing_mapping:
                                    # Update session to match calendar event and create mapping
                                    self.db.update_session(
                                        session_id,
                                        task_name=task_name,
                                        start_time=start_time,
                                        end_time=end_time,
                                        duration_seconds=duration_seconds
                                    )
                                    self.db.set_sync_mapping(session_id, uid, str(event.url))
                                    updated += 1  # Count as update since we linked existing session
                                else:
                                    # Already has mapping, skip to avoid duplicates
                                    skipped += 1
                            else:
                                # Create new session
                                session_id = self.db.add_session(
                                    task_name=task_name,
                                    duration_seconds=duration_seconds,
                                    start_time=start_time,
                                    end_time=end_time,
                                    status='completed',
                                    completed_seconds=duration_seconds
                                )
                                self.db.set_sync_mapping(session_id, uid, str(event.url))
                                imported += 1
                except Exception as e:
                    print(f"Error processing event: {e}")
                    skipped += 1
                    continue
            
            result_msg = f"Found {total_events} events. Imported {imported} new, updated {updated} existing"
            if skipped > 0:
                result_msg += f", skipped {skipped} non-pomodoro events"
            
            return {
                'success': True, 
                'imported': imported, 
                'updated': updated,
                'total_events': total_events,
                'skipped': skipped,
                'message': result_msg
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def sync(self):
        """Perform bidirectional sync"""
        result_to = self.sync_to_calendar()
        result_from = self.sync_from_calendar()
        
        return {
            'success': result_to.get('success', False) and result_from.get('success', False),
            'to_calendar': result_to,
            'from_calendar': result_from
        }

class PomodoroTimer:
    def __init__(self, task_name="Work", duration_seconds=25*60):
        self.task_name = task_name
        self.work_duration = duration_seconds
        self.short_break = 5 * 60  # 5 minutes
        self.long_break = 15 * 60  # 15 minutes
        self.pomodoro_count = 0
        self.is_work_session = True
        self.is_paused = False
        self.start_time = None
        self.remaining_time = self.work_duration
        self.session_start_time = None
        self.db = PomodoroDatabase()
        self.caldav_sync = CalDAVSync(self.db)
        
    def format_time(self, seconds):
        """Format seconds into MM:SS"""
        mins, secs = divmod(int(seconds), 60)
        return f"{mins:02d}:{secs:02d}"
    
    def clear_line(self):
        """Clear the current line in terminal"""
        sys.stdout.write('\r' + ' ' * 80 + '\r')
        sys.stdout.flush()
    
    def beep(self, count=3):
        """Play system beep sound"""
        for _ in range(count):
            print('\a', end='', flush=True)
            time.sleep(0.2)
    
    def display_status(self):
        """Display current timer status"""
        self.clear_line()
        session_type = "WORK" if self.is_work_session else "BREAK"
        time_str = self.format_time(self.remaining_time)
        task_info = f"Task: {self.task_name}" if self.is_work_session else ""
        
        # Create a visual progress bar
        total_time = self.work_duration if self.is_work_session else (
            self.long_break if self.pomodoro_count > 0 and self.pomodoro_count % 4 == 0 else self.short_break
        )
        progress = 1 - (self.remaining_time / total_time)
        bar_length = 30
        filled = int(bar_length * progress)
        bar = '█' * filled + '░' * (bar_length - filled)
        
        status = f"[{session_type}] {time_str} |{bar}| {task_info}"
        sys.stdout.write(status)
        sys.stdout.flush()
    
    def sync_to_calendar_async(self, wait=False):
        """Sync to calendar in a background thread
        
        Args:
            wait: If True, wait for sync to complete (use when exiting)
        """
        import threading
        def sync_worker():
            try:
                if self.caldav_sync.config.get('url'):
                    # Connect if not already connected
                    if not self.caldav_sync.calendar:
                        success, error = self.caldav_sync.connect()
                        if not success:
                            print(f"\n⚠️  Calendar sync failed: {error}")
                            return
                    result = self.caldav_sync.sync_to_calendar()
                    if result.get('success'):
                        synced = result.get('synced', 0)
                        updated = result.get('updated', 0)
                        if synced > 0 or updated > 0:
                            print(f"📅 Calendar synced: {synced} new, {updated} updated")
                        else:
                            print(f"📅 Calendar already up to date")
                    else:
                        print(f"\n⚠️  Calendar sync failed: {result.get('error', 'Unknown error')}")
                else:
                    # No calendar configured - skip silently
                    pass
            except Exception as sync_error:
                print(f"\n⚠️  Calendar sync error: {sync_error}")
        
        thread = threading.Thread(target=sync_worker, daemon=True)
        thread.start()
        
        if wait:
            # Wait for sync to complete (with timeout to avoid hanging forever)
            thread.join(timeout=30)
    
    def start_session(self):
        """Start a work or break session"""
        if self.is_work_session:
            self.remaining_time = self.work_duration
            self.session_start_time = datetime.now()
            duration_str = self.format_time(self.work_duration)
            print(f"\n🍅 Starting work session: {self.task_name} ({duration_str})")
        else:
            if self.pomodoro_count > 0 and self.pomodoro_count % 4 == 0:
                self.remaining_time = self.long_break
                print(f"\n☕ Starting long break (15 min)")
            else:
                self.remaining_time = self.short_break
                print(f"\n☕ Starting short break (5 min)")
        
        self.start_time = time.time()
        self.is_paused = False
        
        try:
            while self.remaining_time > 0:
                if not self.is_paused:
                    self.display_status()
                    time.sleep(1)
                    elapsed = time.time() - self.start_time
                    self.remaining_time = max(0, self.remaining_time - 1)
                else:
                    time.sleep(0.1)
            
            # Session complete
            self.clear_line()
            if self.is_work_session:
                end_time = datetime.now()
                completed_seconds = int(self.work_duration - self.remaining_time)
                self.db.save_session(
                    self.task_name,
                    self.work_duration,
                    self.session_start_time,
                    end_time,
                    "completed",
                    completed_seconds
                )
                # Automatically sync to calendar (async, non-blocking)
                self.sync_to_calendar_async()
                self.pomodoro_count += 1
                print(f"\n✅ Work session complete! Task: {self.task_name}")
                self.beep()
                if self.pomodoro_count % 4 == 0:
                    print("🎉 Great job! Time for a long break!")
                else:
                    print("Take a break!")
                self.is_work_session = False
            else:
                print(f"\n✅ Break complete! Ready for next work session.")
                self.beep()
                self.is_work_session = True
            
            time.sleep(2)
            
        except KeyboardInterrupt:
            self.clear_line()
            print("\n\n⏸ Timer paused. Press Enter to continue, 'q' to quit, 'r' to reset.")
            response = input().strip().lower()
            
            if response == 'q':
                # Save cancelled session
                if self.is_work_session and self.session_start_time:
                    end_time = datetime.now()
                    completed_seconds = int(self.work_duration - self.remaining_time)
                    self.db.save_session(
                        self.task_name,
                        self.work_duration,
                        self.session_start_time,
                        end_time,
                        "cancelled",
                        completed_seconds
                    )
                    # Sync to calendar and wait for it to complete before exiting
                    self.sync_to_calendar_async(wait=True)
                return False
            elif response == 'r':
                self.remaining_time = self.work_duration if self.is_work_session else self.short_break
                self.start_time = time.time()
                if self.is_work_session:
                    self.session_start_time = datetime.now()
                return True
            else:
                # Resume
                self.start_time = time.time() - (self.work_duration - self.remaining_time)
                return True
        
        return True
    
    def run(self):
        """Main loop"""
        print("=" * 60)
        print("🍅 POMODORO TIMER")
        print("=" * 60)
        print(f"Task: {self.task_name}")
        print(f"Duration: {self.format_time(self.work_duration)}")
        print("Press Ctrl+C to pause/resume/reset")
        print("=" * 60)
        
        try:
            while True:
                if not self.start_session():
                    break
        except KeyboardInterrupt:
            self.clear_line()
            # Save cancelled session if interrupted during work
            if self.is_work_session and self.session_start_time:
                end_time = datetime.now()
                completed_seconds = int(self.work_duration - self.remaining_time)
                self.db.save_session(
                    self.task_name,
                    self.work_duration,
                    self.session_start_time,
                    end_time,
                    "cancelled",
                    completed_seconds
                )
                # Sync to calendar and wait for it to complete before exiting
                self.sync_to_calendar_async(wait=True)
            print("\n\n👋 Timer stopped. Good work!")

def parse_duration(duration_str):
    """Parse duration string like '1h', '30m', '2h30m' into seconds"""
    if not duration_str:
        return 25 * 60  # Default 25 minutes
    
    duration_str = duration_str.lower().strip()
    total_seconds = 0
    
    # Match hours and minutes
    hour_match = re.search(r'(\d+)h', duration_str)
    minute_match = re.search(r'(\d+)m', duration_str)
    
    if hour_match:
        total_seconds += int(hour_match.group(1)) * 3600
    
    if minute_match:
        total_seconds += int(minute_match.group(1)) * 60
    
    # If no matches, try to parse as just a number (assume minutes)
    if not hour_match and not minute_match:
        try:
            total_seconds = int(duration_str) * 60  # Assume minutes
        except ValueError:
            return 25 * 60  # Default
    
    return total_seconds if total_seconds > 0 else 25 * 60

def is_port_in_use(port):
    """Check if a port is already in use"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', port))
            return False  # Port is available
        except OSError:
            return True  # Port is in use

def is_airplay_using_port(port):
    """Check if AirPlay Receiver is using the specified port"""
    try:
        # Try to connect and check the Server header
        import urllib.request
        import urllib.error
        try:
            req = urllib.request.Request(f'http://127.0.0.1:{port}/')
            req.add_header('User-Agent', 'Mozilla/5.0')
            with urllib.request.urlopen(req, timeout=1) as response:
                server_header = response.headers.get('Server', '')
                if 'AirTunes' in server_header or 'AirPlay' in server_header:
                    return True
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            # If we can't connect, check the process name
            pass
        
        # Also check process name
        result = subprocess.run(
            ['lsof', '-ti', f':{port}'],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                if pid.strip():
                    try:
                        ps_result = subprocess.run(
                            ['ps', '-p', pid.strip(), '-o', 'command='],
                            capture_output=True,
                            text=True,
                            check=False
                        )
                        process_cmd = ps_result.stdout.strip().lower()
                        if 'airplay' in process_cmd or 'controlcenter' in process_cmd:
                            return True
                    except Exception:
                        pass
    except Exception:
        pass
    return False

def find_available_port(start_port=5000, max_attempts=10):
    """Find an available port starting from start_port"""
    for port in range(start_port, start_port + max_attempts):
        if not is_port_in_use(port):
            return port
    return None

def kill_process_on_port(port):
    """Kill any process using the specified port"""
    try:
        # Use lsof to find the process using the port
        result = subprocess.run(
            ['lsof', '-ti', f':{port}'],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                if pid.strip():
                    try:
                        # Try to get process info to verify it's a pomodoro process
                        ps_result = subprocess.run(
                            ['ps', '-p', pid.strip(), '-o', 'command='],
                            capture_output=True,
                            text=True,
                            check=False
                        )
                        
                        process_cmd = ps_result.stdout.strip().lower()
                        # Check if it's a pomodoro-related process
                        if 'pomodoro' in process_cmd or 'python' in process_cmd:
                            print(f"Found existing process on port {port} (PID: {pid.strip()})")
                            print(f"Process: {process_cmd}")
                            
                            # Kill the process
                            subprocess.run(['kill', '-9', pid.strip()], check=False)
                            print(f"Killed process {pid.strip()}")
                            time.sleep(1.0)  # Give it more time to release the port
                            return True
                    except Exception as e:
                        # If we can't verify, still try to kill it (might be a stale process)
                        print(f"Attempting to kill process {pid.strip()} on port {port}")
                        subprocess.run(['kill', '-9', pid.strip()], check=False)
                        time.sleep(1.0)  # Give it more time to release the port
                        return True
    except FileNotFoundError:
        # lsof not available (unlikely on macOS/Linux, but handle gracefully)
        print("Warning: 'lsof' command not found. Cannot check for existing processes.")
        return False
    except Exception as e:
        print(f"Error checking for processes on port {port}: {e}")
        return False
    
    return False

def start_stats_server():
    """Start Flask server for stats web interface"""
    # Use a less common port to avoid conflicts with AirPlay Receiver and other services
    preferred_port = 8080
    port = preferred_port
    
    if is_port_in_use(preferred_port):
        print(f"Port {preferred_port} is already in use. Checking for existing pomodoro processes...")
        
        # Check if it's AirPlay Receiver (common on macOS)
        if is_airplay_using_port(preferred_port):
            print(f"⚠️  Port {preferred_port} is being used by AirPlay Receiver.")
            print("   This is a common issue on macOS. AirPlay Receiver uses port 5000 by default.")
            print("   To fix this permanently:")
            print("   1. Go to System Settings > General > AirDrop & Handoff")
            print("   2. Turn off 'AirPlay Receiver'")
            print("   Or we can use a different port for the stats server.")
            print()
            print("   Automatically switching to port 5001...")
            port = find_available_port(5001, 10)
            if port is None:
                print("Error: Could not find an available port. Please disable AirPlay Receiver or free up a port.")
                sys.exit(1)
        else:
            # Try to kill existing pomodoro processes
            kill_process_on_port(preferred_port)
            
            # Verify the port is now free
            if is_port_in_use(preferred_port):
                print(f"Port {preferred_port} is still in use by another process.")
                print("Trying to find an alternative port...")
                port = find_available_port(5001, 10)
                if port is None:
                    print("Error: Could not find an available port. Please free up a port.")
                    sys.exit(1)
                print(f"Using alternative port: {port}")
            else:
                print(f"Port {preferred_port} is now available.")
                port = preferred_port
    else:
        print(f"Port {port} is available.")
    
    try:
        from flask import Flask, render_template_string, jsonify, request
    except ImportError:
        print("Flask is required for stats. Install it with: pip install flask")
        sys.exit(1)
    
    app = Flask(__name__)
    db = PomodoroDatabase()
    color_manager = TaskColorManager()
    caldav_sync = CalDAVSync(db)
    
    # Add request logging
    @app.before_request
    def log_request():
        print(f"Request: {request.method} {request.path}")
    
    @app.after_request
    def log_response(response):
        print(f"Response: {response.status_code} for {request.path}")
        return response
    
    @app.route('/health')
    def health():
        """Simple health check endpoint"""
        return jsonify({'status': 'ok', 'message': 'Pomodoro stats server is running'})
    
    @app.route('/')
    def index():
        try:
            return render_template_string(STATS_HTML)
        except NameError as e:
            # STATS_HTML might not be defined yet
            print(f"Error: STATS_HTML not defined: {e}")
            return f"<h1>Error: Stats HTML template not loaded. Please check the application.</h1>", 500
        except Exception as e:
            print(f"Error rendering stats page: {e}")
            import traceback
            traceback.print_exc()
            return f"<h1>Error loading stats page</h1><p>{str(e)}</p>", 500
    
    @app.route('/api/stats')
    def api_stats():
        period = request.args.get('period', 'week')
        stats = db.get_stats(period=period)
        sessions = db.get_all_sessions()
        return jsonify({
            'stats': stats,
            'sessions': sessions
        })
    
    @app.route('/api/task-colors')
    def api_get_task_colors():
        """Get all task colors, generating colors for all existing tasks"""
        # Get all unique task names from sessions
        sessions = db.get_all_sessions()
        task_names = set(session['task_name'] for session in sessions if session.get('task_name'))
        
        # Ensure all tasks have colors
        for task_name in task_names:
            color_manager.get_color(task_name)  # This generates if not exists
        
        return jsonify(color_manager.get_all_colors())
    
    @app.route('/api/task-colors/<path:task_name>', methods=['PUT'])
    def api_update_task_color(task_name):
        """Update color for a specific task"""
        from urllib.parse import unquote
        task_name = unquote(task_name)
        data = request.json
        if 'color' not in data:
            return jsonify({'success': False, 'error': 'Color not provided'}), 400
        color_manager.set_color(task_name, data['color'])
        return jsonify({'success': True, 'color': data['color']})
    
    # Helper function for async calendar sync
    import threading
    def sync_to_calendar_async():
        """Sync to calendar in a background thread"""
        def sync_worker():
            try:
                if caldav_sync.config.get('url'):
                    caldav_sync.sync_to_calendar()
            except Exception as sync_error:
                print(f"Calendar sync error: {sync_error}")
        
        thread = threading.Thread(target=sync_worker, daemon=True)
        thread.start()
    
    @app.route('/api/sessions', methods=['POST'])
    def api_add_session():
        data = request.json
        try:
            start_time = datetime.fromisoformat(data['start_time'].replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(data['end_time'].replace('Z', '+00:00')) if data.get('end_time') else None
            session_id = db.add_session(
                task_name=data['task_name'],
                duration_seconds=int(data['duration_seconds']),
                start_time=start_time,
                end_time=end_time,
                status=data.get('status', 'completed'),
                completed_seconds=int(data.get('completed_seconds', 0))
            )
            # Automatically sync to calendar (async, non-blocking)
            sync_to_calendar_async()
            return jsonify({'success': True, 'id': session_id})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 400
    
    @app.route('/api/sessions/<int:session_id>', methods=['PUT'])
    def api_update_session(session_id):
        data = request.json
        try:
            updates = {}
            if 'task_name' in data:
                updates['task_name'] = data['task_name']
            if 'duration_seconds' in data:
                updates['duration_seconds'] = int(data['duration_seconds'])
            if 'start_time' in data:
                updates['start_time'] = datetime.fromisoformat(data['start_time'].replace('Z', '+00:00'))
            if 'end_time' in data:
                updates['end_time'] = datetime.fromisoformat(data['end_time'].replace('Z', '+00:00')) if data['end_time'] else None
            if 'status' in data:
                updates['status'] = data['status']
            if 'completed_seconds' in data:
                updates['completed_seconds'] = int(data['completed_seconds'])
            
            db.update_session(session_id, **updates)
            # Automatically sync to calendar (async, non-blocking)
            sync_to_calendar_async()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 400
    
    @app.route('/api/sessions/<int:session_id>', methods=['DELETE'])
    def api_delete_session(session_id):
        try:
            # Delete sync mapping if it exists
            db.delete_sync_mapping(session_id=session_id)
            # Delete the session
            db.delete_session(session_id)
            # Automatically sync to calendar to remove the event (async, non-blocking)
            sync_to_calendar_async()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 400
    
    @app.route('/api/export/ical')
    def api_export_ical():
        """Export sessions as iCalendar file"""
        try:
            from icalendar import Calendar, Event
            from flask import Response
            
            sessions = db.get_all_sessions()
            cal = Calendar()
            cal.add('prodid', '-//Pomodoro Timer//EN')
            cal.add('version', '2.0')
            cal.add('calscale', 'GREGORIAN')
            cal.add('method', 'PUBLISH')
            cal.add('X-WR-CALNAME', 'Pomodoro Sessions')
            cal.add('X-WR-CALDESC', 'Pomodoro work sessions')
            
            for session in sessions:
                if session['start_time']:
                    event = Event()
                    start_time = datetime.fromisoformat(session['start_time'].replace('Z', '+00:00'))
                    end_time = session['end_time']
                    if end_time:
                        end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                    else:
                        end_time = start_time + timedelta(seconds=session['duration_seconds'])
                    
                    event.add('summary', f"🍅 {session['task_name']}")
                    event.add('dtstart', start_time)
                    event.add('dtend', end_time)
                    event.add('description', f"Pomodoro session: {session['task_name']}\\nDuration: {session['duration_seconds']//60} minutes\\nCompleted: {session['completed_seconds']//60} minutes\\nStatus: {session['status']}")
                    event.add('uid', f"pomodoro-{session['id']}@pomodoro-timer")
                    cal.add_component(event)
            
            response = Response(cal.to_ical(), mimetype='text/calendar')
            response.headers['Content-Disposition'] = f'attachment; filename=pomodoro-sessions-{datetime.now().strftime("%Y%m%d")}.ics'
            return response
        except ImportError:
            return jsonify({'success': False, 'error': 'icalendar package not installed. Install with: pip install icalendar'}), 500
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/import/ical', methods=['POST'])
    def api_import_ical():
        """Import sessions from iCalendar file"""
        try:
            from icalendar import Calendar
            from flask import request
            
            if 'file' not in request.files:
                return jsonify({'success': False, 'error': 'No file provided'}), 400
            
            file = request.files['file']
            if file.filename == '':
                return jsonify({'success': False, 'error': 'No file selected'}), 400
            
            cal = Calendar.from_ical(file.read())
            imported = 0
            
            for component in cal.walk():
                if component.name == "VEVENT":
                    summary = str(component.get('summary', ''))
                    # Remove emoji if present
                    task_name = summary.replace('🍅', '').strip()
                    if not task_name:
                        task_name = 'Imported Session'
                    
                    dtstart = component.get('dtstart')
                    dtend = component.get('dtend')
                    
                    if dtstart:
                        start_time = dtstart.dt
                        if isinstance(start_time, str):
                            start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                        elif not isinstance(start_time, datetime):
                            continue
                        
                        if dtend:
                            end_time = dtend.dt
                            if isinstance(end_time, str):
                                end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                            elif not isinstance(end_time, datetime):
                                end_time = start_time + timedelta(minutes=25)
                        else:
                            end_time = start_time + timedelta(minutes=25)
                        
                        duration_seconds = int((end_time - start_time).total_seconds())
                        completed_seconds = duration_seconds  # Assume completed if imported
                        
                        # Check if session already exists (by UID or similar time/task)
                        db.add_session(
                            task_name=task_name,
                            duration_seconds=duration_seconds,
                            start_time=start_time,
                            end_time=end_time,
                            status='completed',
                            completed_seconds=completed_seconds
                        )
                        imported += 1
            
            return jsonify({'success': True, 'imported': imported})
        except ImportError:
            return jsonify({'success': False, 'error': 'icalendar package not installed. Install with: pip install icalendar'}), 500
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/caldav/config', methods=['GET'])
    def api_get_caldav_config():
        """Get CalDAV configuration (without password)"""
        config = caldav_sync.config.copy()
        if 'password' in config:
            config['password'] = '***'  # Don't send password back
        return jsonify({'success': True, 'config': config})
    
    @app.route('/api/caldav/config', methods=['POST'])
    def api_set_caldav_config():
        """Set CalDAV configuration"""
        try:
            data = request.json
            url = data.get('url', '').strip()
            username = data.get('username', '').strip()
            password = data.get('password', '').strip()
            calendar_name = data.get('calendar_name', 'Pomodoro Sessions')
            
            # If all fields are empty, clear the configuration
            if not url and not username and not password:
                caldav_sync.config = {}
                caldav_sync.save_config()
                caldav_sync.client = None
                caldav_sync.calendar = None
                return jsonify({'success': True, 'message': 'CalDAV configuration cleared'})
            
            # Otherwise, validate required fields
            if not url or not username or not password:
                return jsonify({'success': False, 'error': 'Missing required fields: url, username, password'}), 400
            
            success, error = caldav_sync.configure(url, username, password, calendar_name)
            if success:
                return jsonify({'success': True, 'message': 'CalDAV configured successfully'})
            else:
                error_msg = error or 'Failed to connect to CalDAV server'
                return jsonify({'success': False, 'error': error_msg}), 500
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/caldav/sync', methods=['POST'])
    def api_caldav_sync():
        """Perform bidirectional CalDAV sync"""
        try:
            result = caldav_sync.sync()
            return jsonify(result)
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/caldav/sync/to', methods=['POST'])
    def api_caldav_sync_to():
        """Sync local sessions to calendar"""
        try:
            result = caldav_sync.sync_to_calendar()
            return jsonify(result)
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/caldav/sync/from', methods=['POST'])
    def api_caldav_sync_from():
        """Sync calendar events to local sessions"""
        try:
            result = caldav_sync.sync_from_calendar()
            return jsonify(result)
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    import webbrowser
    import threading
    
    # Background sync thread - syncs every 5 minutes
    def background_sync():
        """Background thread that periodically syncs with CalDAV"""
        while True:
            try:
                time.sleep(300)  # Wait 5 minutes
                if caldav_sync.config.get('url'):
                    try:
                        result = caldav_sync.sync()
                        if result.get('success'):
                            print(f"Background sync completed: {result}")
                    except Exception as e:
                        print(f"Background sync error: {e}")
            except Exception as e:
                print(f"Background sync thread error: {e}")
                time.sleep(60)  # Wait 1 minute before retrying
    
    # Start background sync thread
    sync_thread = threading.Thread(target=background_sync, daemon=True)
    sync_thread.start()
    
    # Open browser after server is ready
    def open_browser():
        # Wait a bit longer to ensure server is fully started
        time.sleep(2.5)
        try:
            webbrowser.open(f'http://127.0.0.1:{port}')
        except Exception as e:
            print(f"Could not open browser automatically: {e}")
            print(f"Please manually open http://127.0.0.1:{port} in your browser")
    
    threading.Thread(target=open_browser, daemon=True).start()
    
    print(f"Starting stats server at http://127.0.0.1:{port}")
    print("CalDAV background sync enabled (every 5 minutes)")
    print("Press Ctrl+C to stop the server")
    try:
        app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"\nError: Port {port} is still in use. Please manually stop the process using this port.")
            print("You can find it with: lsof -ti :5000")
            sys.exit(1)
        else:
            raise

STATS_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pomodoro Stats</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            color: #333;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        
        h1 {
            color: white;
            text-align: center;
            margin-bottom: 30px;
            font-size: 2.5em;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }
        
        .period-selector {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            justify-content: center;
        }
        
        .period-btn {
            padding: 10px 20px;
            border: 2px solid white;
            background: rgba(255,255,255,0.2);
            color: white;
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.9em;
            font-weight: bold;
            transition: all 0.2s;
        }
        
        .period-btn.active {
            background: white;
            color: #667eea;
        }
        
        .period-btn:hover {
            background: rgba(255,255,255,0.3);
        }
        
        .task-breakdown {
            background: white;
            border-radius: 12px;
            padding: 15px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            margin-bottom: 30px;
        }
        
        .task-breakdown h2 {
            color: #333;
            margin-bottom: 12px;
            font-size: 1.4em;
        }
        
        .task-list-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 0;
            border-bottom: 1px solid #f0f0f0;
        }
        
        .task-list-item:last-child {
            border-bottom: none;
        }
        
        .task-name-large {
            font-weight: 600;
            color: #333;
            font-size: 0.95em;
        }
        
        .task-time-large {
            color: #667eea;
            font-weight: 600;
            font-size: 0.9em;
        }
        
        .no-tasks {
            text-align: center;
            padding: 20px;
            color: #999;
            font-size: 0.95em;
        }
        
        .section {
            background: white;
            border-radius: 12px;
            padding: 25px;
            margin-bottom: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        
        .section h2 {
            color: #333;
            margin-bottom: 20px;
            font-size: 1.8em;
        }
        
        .calendar-controls {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            flex-wrap: wrap;
            gap: 10px;
        }
        
        .view-toggle {
            display: flex;
            gap: 10px;
        }
        
        .view-btn {
            padding: 10px 20px;
            border: 2px solid #667eea;
            background: white;
            color: #667eea;
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.9em;
            font-weight: bold;
            transition: all 0.2s;
        }
        
        .view-btn.active {
            background: #667eea;
            color: white;
        }
        
        .view-btn:hover {
            background: #5568d3;
            color: white;
        }
        
        .nav-tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            border-bottom: 2px solid rgba(255, 255, 255, 0.3);
            padding-bottom: 10px;
        }
        
        .nav-tab {
            padding: 12px 24px;
            border: none;
            background: transparent;
            color: rgba(255, 255, 255, 0.9);
            border-radius: 8px 8px 0 0;
            cursor: pointer;
            font-size: 1em;
            font-weight: 500;
            transition: all 0.2s;
            border-bottom: 3px solid transparent;
        }
        
        .nav-tab.active {
            color: white;
            border-bottom-color: white;
            background: rgba(255, 255, 255, 0.15);
        }
        
        .nav-tab:hover {
            color: white;
            background: rgba(255, 255, 255, 0.1);
        }
        
        .settings-section {
            margin-bottom: 30px;
            padding: 20px;
            background: #f9f9f9;
            border-radius: 8px;
            border: 1px solid #e0e0e0;
        }
        
        .settings-section h3 {
            color: #333;
            margin-bottom: 15px;
        }
        
        .nav-buttons {
            display: flex;
            gap: 10px;
        }
        
        .nav-btn {
            padding: 10px 15px;
            border: 2px solid #667eea;
            background: white;
            color: #667eea;
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.9em;
            transition: all 0.2s;
        }
        
        .nav-btn:hover {
            background: #667eea;
            color: white;
        }
        
        .calendar-title {
            font-size: 1.3em;
            font-weight: bold;
            color: #333;
        }
        
        .calendar {
            display: grid;
            grid-template-columns: repeat(7, 1fr);
            gap: 10px;
            margin-top: 20px;
        }
        
        .calendar.daily-view {
            grid-template-columns: 1fr;
        }
        
        .calendar.weekly-view {
            grid-template-columns: repeat(7, 1fr);
        }
        
        .calendar.monthly-view {
            grid-template-columns: repeat(7, 1fr);
        }
        
        .calendar.yearly-view {
            grid-template-columns: repeat(7, 1fr);
            max-width: 100%;
            overflow-x: auto;
        }
        
        .calendar-header {
            font-weight: bold;
            text-align: center;
            padding: 10px;
            color: #667eea;
        }
        
        .calendar-day {
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            padding: 8px;
            text-align: left;
            position: relative;
            background: #f9f9f9;
            transition: all 0.3s ease;
            min-height: 100px;
            display: flex;
            flex-direction: column;
        }
        
        .calendar-day.daily-view {
            aspect-ratio: auto;
            min-height: 400px;
            max-height: 80vh;
            overflow-y: auto;
        }
        
        .calendar-day.today {
            border-color: #667eea;
            border-width: 3px;
        }
        
        .calendar-day:hover {
            transform: scale(1.02);
            box-shadow: 0 4px 8px rgba(0,0,0,0.2);
            cursor: pointer;
        }
        
        .calendar-day.clickable {
            cursor: pointer;
        }
        
        .calendar-day.has-work {
            /* Individual tasks will be colored, not the entire day */
            border-color: #667eea;
            border-width: 2px;
        }
        
        .day-number {
            font-size: 1.2em;
            font-weight: bold;
            text-align: center;
            margin-bottom: 4px;
        }
        
        .day-work-time {
            font-size: 0.85em;
            margin-top: 4px;
            margin-bottom: 6px;
            opacity: 0.95;
            text-align: center;
        }
        
        .day-tasks-breakdown {
            flex: 1;
            overflow-y: auto;
            scrollbar-width: thin;
            scrollbar-color: rgba(255,255,255,0.3) transparent;
        }
        
        .day-tasks-breakdown::-webkit-scrollbar {
            width: 4px;
        }
        
        .day-tasks-breakdown::-webkit-scrollbar-track {
            background: transparent;
        }
        
        .day-tasks-breakdown::-webkit-scrollbar-thumb {
            background: rgba(255,255,255,0.3);
            border-radius: 2px;
        }
        
        .day-sessions {
            margin-top: 10px;
            font-size: 0.85em;
        }
        
        .session-item-small {
            padding: 4px 8px;
            margin: 4px 0;
            background: rgba(255,255,255,0.2);
            border-radius: 4px;
            font-size: 0.85em;
        }
        
        .day-sessions {
            width: 100%;
        }
        
        .sync-section {
            display: flex;
            gap: 10px;
            margin-top: 10px;
            flex-wrap: wrap;
        }
        
        .btn-sync {
            background: #f39c12;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.9em;
            transition: all 0.2s;
        }
        
        .btn-sync:hover {
            background: #e67e22;
        }
        
        .btn-export {
            background: #3498db;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.9em;
            transition: all 0.2s;
        }
        
        .btn-export:hover {
            background: #2980b9;
        }
        
        .task-list {
            list-style: none;
        }
        
        .task-item {
            padding: 15px;
            margin-bottom: 10px;
            background: #f5f5f5;
            border-radius: 8px;
            border-left: 4px solid #667eea;
        }
        
        .task-name {
            font-weight: bold;
            color: #333;
            margin-bottom: 5px;
        }
        
        .task-stats {
            color: #666;
            font-size: 0.9em;
        }
        
        .loading {
            text-align: center;
            padding: 40px;
            color: white;
            font-size: 1.2em;
        }
        
        .sessions-list {
            list-style: none;
        }
        
        .session-item {
            padding: 15px;
            margin-bottom: 10px;
            background: #f5f5f5;
            border-radius: 8px;
            border-left: 4px solid #667eea;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .session-info {
            flex: 1;
        }
        
        .session-actions {
            display: flex;
            gap: 10px;
        }
        
        .btn {
            padding: 8px 16px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.9em;
            transition: all 0.2s;
        }
        
        .btn-edit {
            background: #667eea;
            color: white;
        }
        
        .btn-edit:hover {
            background: #5568d3;
        }
        
        .btn-delete {
            background: #e74c3c;
            color: white;
        }
        
        .btn-delete:hover {
            background: #c0392b;
        }
        
        .btn-add {
            background: #27ae60;
            color: white;
            padding: 12px 24px;
            font-size: 1em;
            margin-bottom: 20px;
        }
        
        .btn-add:hover {
            background: #229954;
        }
        
        .form-section {
            background: white;
            border-radius: 12px;
            padding: 25px;
            margin-bottom: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        
        .form-group {
            margin-bottom: 15px;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 5px;
            color: #333;
            font-weight: bold;
        }
        
        .form-group input,
        .form-group select {
            width: 100%;
            padding: 10px;
            border: 2px solid #e0e0e0;
            border-radius: 6px;
            font-size: 1em;
        }
        
        .form-group input:focus,
        .form-group select:focus {
            outline: none;
            border-color: #667eea;
        }
        
        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
        }
        
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            z-index: 1000;
            justify-content: center;
            align-items: center;
        }
        
        .modal-content {
            background: white;
            border-radius: 12px;
            padding: 30px;
            max-width: 500px;
            width: 90%;
            max-height: 90vh;
            overflow-y: auto;
        }
        
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }
        
        .modal-header h2 {
            margin: 0;
        }
        
        .close-btn {
            background: none;
            border: none;
            font-size: 1.5em;
            cursor: pointer;
            color: #666;
        }
        
        .close-btn:hover {
            color: #333;
        }
        
        .color-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px;
            margin-bottom: 10px;
            background: #f5f5f5;
            border-radius: 8px;
            border-left: 4px solid #667eea;
        }
        
        .color-item-info {
            display: flex;
            align-items: center;
            gap: 12px;
            flex: 1;
        }
        
        .color-preview {
            width: 40px;
            height: 40px;
            border-radius: 6px;
            border: 2px solid #ddd;
            cursor: pointer;
        }
        
        .color-picker-input {
            width: 60px;
            height: 40px;
            border: 2px solid #ddd;
            border-radius: 6px;
            cursor: pointer;
        }
        
        .task-name-color {
            font-weight: 600;
            color: #333;
        }
        
        .btn-customize-colors {
            background: #9b59b6;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.9em;
            transition: all 0.2s;
            margin-left: 10px;
        }
        
        .btn-customize-colors:hover {
            background: #8e44ad;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🍅 Pomodoro Statistics</h1>
        
        <div id="loading" class="loading">Loading statistics...</div>
        
        <!-- Navigation Tabs -->
        <div class="nav-tabs" id="nav-tabs" style="display: none; margin-bottom: 20px;">
            <button class="nav-tab active" onclick="showView('stats')" id="nav-stats">📊 Stats</button>
            <button class="nav-tab" onclick="showView('settings')" id="nav-settings">⚙️ Settings</button>
        </div>
        
        <!-- Stats View -->
        <div id="stats-view" style="display: none;">
            <div class="period-selector">
                <button class="period-btn active" onclick="changePeriod('week')" id="period-week">Week</button>
                <button class="period-btn" onclick="changePeriod('month')" id="period-month">Month</button>
                <button class="period-btn" onclick="changePeriod('year')" id="period-year">Year</button>
                <button class="period-btn" onclick="changePeriod('all')" id="period-all">All Time</button>
            </div>
            
            <div class="task-breakdown">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <h2 style="margin: 0;">📊 Task Overview</h2>
                    <button class="btn-customize-colors" onclick="showColorsModal()">🎨 Customize Colors</button>
                </div>
                <div id="task-breakdown-content"></div>
            </div>
            
            <div class="section">
                <h2>📅 Calendar</h2>
                <div class="calendar-controls">
                    <div class="calendar-title" id="calendar-title"></div>
                    <div class="nav-buttons">
                        <button class="nav-btn" onclick="navigateCalendar(-1)">← Prev</button>
                        <button class="nav-btn" onclick="navigateCalendar(0)">Today</button>
                        <button class="nav-btn" onclick="navigateCalendar(1)">Next →</button>
                    </div>
                </div>
                <div id="calendar"></div>
            </div>
        </div>
        
        <!-- Settings View -->
        <div id="settings-view" style="display: none;">
            <div class="section">
                <h2>⚙️ Settings</h2>
                
                <!-- Calendar Sync Section -->
                <div class="settings-section">
                    <h3 style="margin-top: 0;">🔄 Calendar Sync</h3>
                    <p style="color: #666; font-size: 0.9em; margin: 10px 0;">
                        Set up automatic bidirectional sync with iPhone Calendar, iCloud, Google Calendar, or any CalDAV-compatible calendar.
                    </p>
                    
                    <!-- Current Connection Info (if configured) -->
                    <div id="caldav-current-connection" style="display: none; margin: 15px 0; padding: 15px; background: rgba(40,167,69,0.1); border: 1px solid rgba(40,167,69,0.3); border-radius: 4px;">
                        <h4 style="margin-top: 0; color: #28a745;">✓ Calendar Sync Configured</h4>
                        <div id="caldav-connection-info" style="margin: 10px 0; font-size: 0.9em;">
                            <!-- Connection details will be populated here -->
                        </div>
                        <div style="margin-top: 15px;">
                            <button class="btn btn-add" onclick="syncCalDAV()" style="margin-right: 10px;">🔄 Sync Now</button>
                            <button class="btn btn-export" onclick="syncCalDAVTo()" style="margin-right: 10px;">📤 Push to Calendar</button>
                            <button class="btn btn-add" onclick="syncCalDAVFrom()" style="margin-right: 10px;">📥 Pull from Calendar</button>
                            <button class="btn" onclick="startCalDAVWizard()" style="background: #ffc107; color: #000;">⚙️ Reconfigure</button>
                            <button class="btn" onclick="clearCalDAVConfig()" style="margin-left: 10px; background: #dc3545;">Clear Config</button>
                        </div>
                    </div>
                    
                    <!-- Step-by-Step Setup Wizard -->
                    <div id="caldav-wizard" style="display: none; margin: 20px 0;">
                        <!-- Step Indicator -->
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; position: relative; padding: 0 10px;">
                            <div style="flex: 1; text-align: center; position: relative; z-index: 2;">
                                <div id="wizard-step-1-indicator" class="wizard-step-indicator active" style="width: 40px; height: 40px; border-radius: 50%; background: #007bff; color: white; display: inline-flex; align-items: center; justify-content: center; font-weight: bold; margin-bottom: 5px; border: 3px solid white; box-shadow: 0 0 0 2px #007bff;">1</div>
                                <div style="font-size: 0.85em; color: #666;">Choose Provider</div>
                            </div>
                            <div style="flex: 1; height: 2px; background: #ddd; margin: 0 -10px; position: relative; top: -15px; z-index: 1;"></div>
                            <div style="flex: 1; text-align: center; position: relative; z-index: 2;">
                                <div id="wizard-step-2-indicator" class="wizard-step-indicator" style="width: 40px; height: 40px; border-radius: 50%; background: #ddd; color: #666; display: inline-flex; align-items: center; justify-content: center; font-weight: bold; margin-bottom: 5px; border: 3px solid white; box-shadow: 0 0 0 2px #ddd;">2</div>
                                <div style="font-size: 0.85em; color: #666;">Enter Credentials</div>
                            </div>
                            <div style="flex: 1; height: 2px; background: #ddd; margin: 0 -10px; position: relative; top: -15px; z-index: 1;"></div>
                            <div style="flex: 1; text-align: center; position: relative; z-index: 2;">
                                <div id="wizard-step-3-indicator" class="wizard-step-indicator" style="width: 40px; height: 40px; border-radius: 50%; background: #ddd; color: #666; display: inline-flex; align-items: center; justify-content: center; font-weight: bold; margin-bottom: 5px; border: 3px solid white; box-shadow: 0 0 0 2px #ddd;">3</div>
                                <div style="font-size: 0.85em; color: #666;">Test Connection</div>
                            </div>
                            <div style="flex: 1; height: 2px; background: #ddd; margin: 0 -10px; position: relative; top: -15px; z-index: 1;"></div>
                            <div style="flex: 1; text-align: center; position: relative; z-index: 2;">
                                <div id="wizard-step-4-indicator" class="wizard-step-indicator" style="width: 40px; height: 40px; border-radius: 50%; background: #ddd; color: #666; display: inline-flex; align-items: center; justify-content: center; font-weight: bold; margin-bottom: 5px; border: 3px solid white; box-shadow: 0 0 0 2px #ddd;">4</div>
                                <div style="font-size: 0.85em; color: #666;">Complete</div>
                            </div>
                        </div>
                        
                        <!-- Step 1: Choose Provider -->
                        <div id="wizard-step-1" class="wizard-step" style="display: block;">
                            <h4 style="margin-top: 0;">Step 1: Choose Your Calendar Provider</h4>
                            <p style="color: #666; font-size: 0.9em; margin-bottom: 20px;">Select the calendar service you want to sync with:</p>
                            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px;">
                                <div id="provider-icloud" class="provider-option" onclick="selectProvider('icloud', this)" style="padding: 20px; border: 2px solid #ddd; border-radius: 8px; cursor: pointer; text-align: center; transition: all 0.3s;" onmouseover="if(!this.classList.contains('selected')) {this.style.borderColor='#007bff'; this.style.background='rgba(0,123,255,0.05)'}" onmouseout="if(!this.classList.contains('selected')) {this.style.borderColor='#ddd'; this.style.background='transparent'}">
                                    <div style="font-size: 2em; margin-bottom: 10px;">☁️</div>
                                    <strong>iCloud</strong>
                                    <div style="font-size: 0.85em; color: #666; margin-top: 5px;">iPhone Calendar</div>
                                </div>
                                <div id="provider-google" class="provider-option" onclick="selectProvider('google', this)" style="padding: 20px; border: 2px solid #ddd; border-radius: 8px; cursor: pointer; text-align: center; transition: all 0.3s;" onmouseover="if(!this.classList.contains('selected')) {this.style.borderColor='#007bff'; this.style.background='rgba(0,123,255,0.05)'}" onmouseout="if(!this.classList.contains('selected')) {this.style.borderColor='#ddd'; this.style.background='transparent'}">
                                    <div style="font-size: 2em; margin-bottom: 10px;">📅</div>
                                    <strong>Google Calendar</strong>
                                    <div style="font-size: 0.85em; color: #666; margin-top: 5px;">Gmail Calendar</div>
                                </div>
                                <div id="provider-other" class="provider-option" onclick="selectProvider('other', this)" style="padding: 20px; border: 2px solid #ddd; border-radius: 8px; cursor: pointer; text-align: center; transition: all 0.3s;" onmouseover="if(!this.classList.contains('selected')) {this.style.borderColor='#007bff'; this.style.background='rgba(0,123,255,0.05)'}" onmouseout="if(!this.classList.contains('selected')) {this.style.borderColor='#ddd'; this.style.background='transparent'}">
                                    <div style="font-size: 2em; margin-bottom: 10px;">⚙️</div>
                                    <strong>Other CalDAV</strong>
                                    <div style="font-size: 0.85em; color: #666; margin-top: 5px;">Custom Server</div>
                                </div>
                            </div>
                            <div style="margin-top: 20px;">
                                <button class="btn btn-add" onclick="wizardNextStep()" id="wizard-step-1-next" disabled style="opacity: 0.5; cursor: not-allowed;">Next →</button>
                                <button class="btn" onclick="cancelCalDAVWizard()" style="margin-left: 10px; background: #6c757d;">Cancel</button>
                            </div>
                        </div>
                        
                        <!-- Step 2: Enter Credentials -->
                        <div id="wizard-step-2" class="wizard-step" style="display: none;">
                            <h4 style="margin-top: 0;">Step 2: Enter Your Credentials</h4>
                            <div id="wizard-provider-instructions" style="padding: 15px; background: rgba(0,123,255,0.1); border-radius: 4px; margin-bottom: 20px; font-size: 0.9em;">
                                <!-- Instructions will be populated here -->
                            </div>
                            <div id="wizard-icloud-instructions" style="display: none;">
                                <strong>iCloud Setup Instructions:</strong>
                                <ol style="margin: 10px 0; padding-left: 20px;">
                                    <li>Use your Apple ID email address as the username</li>
                                    <li>You'll need to generate an App-Specific Password:
                                        <ul style="margin-top: 5px;">
                                            <li>Go to <a href="https://appleid.apple.com" target="_blank">appleid.apple.com</a></li>
                                            <li>Sign in and go to "Sign-In and Security"</li>
                                            <li>Click "App-Specific Passwords"</li>
                                            <li>Generate a new password and use it here</li>
                                        </ul>
                                    </li>
                                </ol>
                            </div>
                            <div id="wizard-google-instructions" style="display: none;">
                                <strong>Google Calendar Setup Instructions:</strong>
                                <ol style="margin: 10px 0; padding-left: 20px;">
                                    <li>Use your Gmail address as the username</li>
                                    <li>You'll need to create an App Password:
                                        <ul style="margin-top: 5px;">
                                            <li>Go to your <a href="https://myaccount.google.com/apppasswords" target="_blank">Google Account settings</a></li>
                                            <li>Enable 2-Step Verification if not already enabled</li>
                                            <li>Go to "App passwords" and generate a new password</li>
                                            <li>Use that password here (not your regular Gmail password)</li>
                                        </ul>
                                    </li>
                                    <li>The URL will be automatically set to: <code>https://apidata.googleusercontent.com/caldav/v2/[your-email]/events/</code></li>
                                </ol>
                            </div>
                            <div id="wizard-other-instructions" style="display: none;">
                                <strong>Custom CalDAV Server:</strong>
                                <p style="margin: 10px 0;">Enter your CalDAV server details. Contact your administrator or check your calendar provider's documentation for the correct server URL.</p>
                            </div>
                            <div style="margin-top: 20px;">
                                <label>Server URL</label>
                                <input type="text" id="wizard-caldav-url" placeholder="https://caldav.icloud.com" style="width: 100%; padding: 10px; margin-bottom: 15px; border: 1px solid #ddd; border-radius: 4px;">
                                <label>Username/Email</label>
                                <input type="text" id="wizard-caldav-username" placeholder="your@email.com" style="width: 100%; padding: 10px; margin-bottom: 15px; border: 1px solid #ddd; border-radius: 4px;">
                                <label>Password/App Password</label>
                                <input type="password" id="wizard-caldav-password" placeholder="Enter your password or app password" style="width: 100%; padding: 10px; margin-bottom: 15px; border: 1px solid #ddd; border-radius: 4px;">
                                <label>Calendar Name (optional)</label>
                                <input type="text" id="wizard-caldav-calendar-name" placeholder="Pomodoro Sessions" value="Pomodoro Sessions" style="width: 100%; padding: 10px; margin-bottom: 15px; border: 1px solid #ddd; border-radius: 4px;">
                            </div>
                            <div style="margin-top: 20px;">
                                <button class="btn" onclick="wizardPreviousStep()">← Previous</button>
                                <button class="btn btn-add" onclick="wizardNextStep()" style="margin-left: 10px;">Next →</button>
                                <button class="btn" onclick="cancelCalDAVWizard()" style="margin-left: 10px; background: #6c757d;">Cancel</button>
                            </div>
                        </div>
                        
                        <!-- Step 3: Test Connection -->
                        <div id="wizard-step-3" class="wizard-step" style="display: none;">
                            <h4 style="margin-top: 0;">Step 3: Test Connection</h4>
                            <p style="color: #666; font-size: 0.9em; margin-bottom: 20px;">Let's verify your connection works before saving:</p>
                            <div id="wizard-test-status" style="margin: 20px 0; padding: 15px; background: rgba(0,123,255,0.1); border-radius: 4px; display: none;">
                                <span id="wizard-test-status-text"></span>
                            </div>
                            <div style="margin-top: 20px;">
                                <button class="btn btn-export" onclick="wizardTestConnection()" id="wizard-test-btn">Test Connection</button>
                            </div>
                            <div style="margin-top: 20px;">
                                <button class="btn" onclick="wizardPreviousStep()">← Previous</button>
                                <button class="btn btn-add" onclick="wizardNextStep()" id="wizard-step-3-next" disabled style="opacity: 0.5; cursor: not-allowed; margin-left: 10px;">Save & Complete →</button>
                                <button class="btn" onclick="cancelCalDAVWizard()" style="margin-left: 10px; background: #6c757d;">Cancel</button>
                            </div>
                        </div>
                        
                        <!-- Step 4: Complete -->
                        <div id="wizard-step-4" class="wizard-step" style="display: none;">
                            <h4 style="margin-top: 0; color: #28a745;">✓ Setup Complete!</h4>
                            <div style="padding: 20px; background: rgba(40,167,69,0.1); border-radius: 4px; margin: 20px 0; text-align: center;">
                                <div style="font-size: 3em; margin-bottom: 10px;">🎉</div>
                                <p style="font-size: 1.1em; margin: 10px 0;">Your calendar sync is now configured!</p>
                                <p style="color: #666; font-size: 0.9em;">You can now sync your Pomodoro sessions with your calendar.</p>
                            </div>
                            <div style="margin-top: 20px;">
                                <button class="btn btn-add" onclick="wizardComplete()" style="width: 100%;">Done</button>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Start Setup Button (shown when not configured) -->
                    <div id="caldav-start-setup" style="margin: 20px 0;">
                        <button class="btn btn-add" onclick="startCalDAVWizard()" style="font-size: 1.1em; padding: 15px 30px;">🚀 Start Calendar Sync Setup</button>
                    </div>
                    
                    <!-- Status Messages -->
                    <div id="caldav-status" style="margin: 10px 0; padding: 10px; background: rgba(0,0,0,0.05); border-radius: 4px; display: none;">
                        <span id="caldav-status-text"></span>
                    </div>
                </div>
                
                <hr style="margin: 30px 0;">
                
                <!-- Manual Import/Export Section -->
                <div class="settings-section">
                    <h3 style="margin-top: 0;">📥 Import/Export iCalendar</h3>
                    <div style="margin-bottom: 15px;">
                        <label>Import iCalendar File</label>
                        <input type="file" id="ical-file" accept=".ics" style="padding: 10px; width: 100%; margin-top: 5px;">
                        <button class="btn btn-add" onclick="importICalendar()" style="margin-top: 10px;">Import</button>
                    </div>
                    <div>
                        <label>Export iCalendar File</label>
                        <button class="btn btn-export" onclick="exportICalendar();">Export</button>
                    </div>
                </div>
            </div>
        </div>
        
    </div>
    
    <!-- Sync Calendar Modal -->
    <div id="sync-modal" class="modal">
        <div class="modal-content" style="max-width: 600px;">
            <div class="modal-header">
                <h2>Sync Calendar</h2>
                <button class="close-btn" onclick="closeSyncModal()">&times;</button>
            </div>
            <div style="padding: 20px 0;">
                <!-- CalDAV Sync Section -->
                <div class="form-group">
                    <h3 style="margin-top: 0;">🔄 Real-time Sync (CalDAV)</h3>
                    <p style="color: #666; font-size: 0.9em; margin: 10px 0;">
                        Set up automatic bidirectional sync with iPhone Calendar, iCloud, Google Calendar, or any CalDAV-compatible calendar.
                    </p>
                    <div id="caldav-status" style="margin: 10px 0; padding: 10px; background: rgba(0,0,0,0.05); border-radius: 4px; display: none;">
                        <span id="caldav-status-text"></span>
                    </div>
                    <div id="caldav-config-form">
                        <label>CalDAV Server URL</label>
                        <input type="text" id="caldav-url" placeholder="https://caldav.icloud.com or https://apidata.googleusercontent.com/caldav/v2/..." style="width: 100%; padding: 10px; margin-bottom: 10px; border: 1px solid #ddd; border-radius: 4px;">
                        <label>Username/Email</label>
                        <input type="text" id="caldav-username" placeholder="your@email.com" style="width: 100%; padding: 10px; margin-bottom: 10px; border: 1px solid #ddd; border-radius: 4px;">
                        <label>Password/App Password</label>
                        <input type="password" id="caldav-password" placeholder="Password or App Password" style="width: 100%; padding: 10px; margin-bottom: 10px; border: 1px solid #ddd; border-radius: 4px;">
                        <label>Calendar Name (optional)</label>
                        <input type="text" id="caldav-calendar-name" placeholder="Pomodoro Sessions" value="Pomodoro Sessions" style="width: 100%; padding: 10px; margin-bottom: 10px; border: 1px solid #ddd; border-radius: 4px;">
                        <div style="margin-top: 15px;">
                            <button class="btn btn-add" onclick="saveCalDAVConfig()">Save Configuration</button>
                            <button class="btn btn-export" onclick="testCalDAVConnection()" style="margin-left: 10px;">Test Connection</button>
                        </div>
                    </div>
                    <div id="caldav-sync-controls" style="display: none; margin-top: 15px;">
                        <button class="btn btn-add" onclick="syncCalDAV()">🔄 Sync Now</button>
                        <button class="btn btn-export" onclick="syncCalDAVTo()" style="margin-left: 10px;">📤 Push to Calendar</button>
                        <button class="btn btn-add" onclick="syncCalDAVFrom()" style="margin-left: 10px;">📥 Pull from Calendar</button>
                        <button class="btn" onclick="clearCalDAVConfig()" style="margin-left: 10px; background: #dc3545;">Clear Config</button>
                    </div>
                    <div style="margin-top: 20px; padding: 15px; background: rgba(0,123,255,0.1); border-radius: 4px; font-size: 0.9em;">
                        <strong>Quick Setup Guides:</strong><br>
                        <strong>iCloud:</strong> URL: https://caldav.icloud.com, use your Apple ID<br>
                        <strong>Google Calendar:</strong> Use App Password, URL: https://apidata.googleusercontent.com/caldav/v2/[your-email]/events/<br>
                        <strong>iPhone Calendar:</strong> Uses iCloud by default - configure iCloud above
                    </div>
                </div>
                <hr style="margin: 30px 0;">
                <!-- Manual Import/Export Section -->
                <div class="form-group">
                    <h3 style="margin-top: 0;">📥 Manual Import/Export</h3>
                    <div style="margin-bottom: 15px;">
                        <label>Import iCalendar File</label>
                        <input type="file" id="ical-file" accept=".ics" style="padding: 10px; width: 100%; margin-top: 5px;">
                        <button class="btn btn-add" onclick="importICalendar()" style="margin-top: 10px;">Import</button>
                    </div>
                    <div>
                        <label>Export iCalendar File</label>
                        <button class="btn btn-export" onclick="exportICalendar();">Export</button>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Day Sessions Modal -->
    <div id="day-sessions-modal" class="modal">
        <div class="modal-content" id="day-sessions-content">
        </div>
    </div>
    
    <!-- Add/Edit Session Modal -->
    <div id="session-modal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <h2 id="modal-title">Add Session</h2>
                <button class="close-btn" onclick="closeModal()">&times;</button>
            </div>
            <form id="session-form" onsubmit="saveSession(event)">
                <input type="hidden" id="session-id" value="">
                <div class="form-group">
                    <label for="task-name">Task Name</label>
                    <input type="text" id="task-name" required>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label for="start-time">Start Time</label>
                        <input type="datetime-local" id="start-time" required>
                    </div>
                    <div class="form-group">
                        <label for="end-time">End Time</label>
                        <input type="datetime-local" id="end-time">
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label for="duration">Duration (minutes)</label>
                        <input type="number" id="duration" min="1" required>
                    </div>
                    <div class="form-group">
                        <label for="completed-time">Completed Time (minutes)</label>
                        <input type="number" id="completed-time" min="0" required>
                    </div>
                </div>
                <div class="form-group">
                    <label for="status">Status</label>
                    <select id="status" required>
                        <option value="completed">Completed</option>
                        <option value="cancelled">Cancelled</option>
                    </select>
                </div>
                <div style="display: flex; gap: 10px; margin-top: 20px;">
                    <button type="submit" class="btn btn-add" style="flex: 1;">Save</button>
                    <button type="button" class="btn" onclick="closeModal()" style="flex: 1; background: #95a5a6; color: white;">Cancel</button>
                </div>
            </form>
        </div>
    </div>
    
    <!-- Task Colors Modal -->
    <div id="colors-modal" class="modal">
        <div class="modal-content" style="max-width: 600px;">
            <div class="modal-header">
                <h2>Customize Task Colors</h2>
                <button class="close-btn" onclick="closeColorsModal()">&times;</button>
            </div>
            <div style="padding: 20px 0;">
                <div id="colors-list" style="max-height: 60vh; overflow-y: auto;"></div>
            </div>
        </div>
    </div>
    
    <script>
        let currentPeriod = 'week';
        let currentView = 'weekly';
        let currentDate = new Date();
        let allSessions = [];
        let taskColors = {};
        
        function loadTaskColors() {
            fetch('/api/task-colors')
                .then(response => response.json())
                .then(colors => {
                    taskColors = colors;
                    // Ensure all tasks have colors
                    allSessions.forEach(session => {
                        if (session.task_name && !taskColors[session.task_name]) {
                            // Color will be generated on backend when needed
                        }
                    });
                })
                .catch(error => {
                    console.error('Error loading task colors:', error);
                });
        }
        
        function getTaskColor(taskName) {
            if (!taskName) return '#667eea';
            if (taskColors[taskName]) {
                return taskColors[taskName];
            }
            // Generate a temporary color if not loaded yet
            return generateTempColor(taskName);
        }
        
        function generateTempColor(taskName) {
            let hash = 0;
            for (let i = 0; i < taskName.length; i++) {
                hash = taskName.charCodeAt(i) + ((hash << 5) - hash);
            }
            const hue = Math.abs(hash % 360);
            const saturation = 60 + (Math.abs(hash) % 30);
            const lightness = 45 + (Math.abs(hash) % 20);
            return `hsl(${hue}, ${saturation}%, ${lightness}%)`;
        }
        
        function displayTaskBreakdown(sessions) {
            const breakdownContent = document.getElementById('task-breakdown-content');
            
            // Calculate task breakdown from sessions
            const taskBreakdown = {};
            sessions.forEach(session => {
                if (!session.start_time) return;
                const taskName = session.task_name || 'Unknown';
                const timeSeconds = session.completed_seconds || 0;
                
                if (!taskBreakdown[taskName]) {
                    taskBreakdown[taskName] = {
                        task_name: taskName,
                        total_time: 0,
                        count: 0
                    };
                }
                taskBreakdown[taskName].total_time += timeSeconds;
                taskBreakdown[taskName].count += 1;
            });
            
            const tasks = Object.values(taskBreakdown);
            
            if (tasks.length === 0) {
                breakdownContent.innerHTML = '<div class="no-tasks">No sessions for this period. Start working to see your task breakdown!</div>';
                return;
            }
            
            // Sort tasks by time (descending)
            const sortedTasks = [...tasks].sort((a, b) => (b.total_time || 0) - (a.total_time || 0));
            
            breakdownContent.innerHTML = '';
            
            sortedTasks.forEach((task) => {
                const timeSeconds = task.total_time || 0;
                const taskColor = getTaskColor(task.task_name);
                
                const taskItem = document.createElement('div');
                taskItem.className = 'task-list-item';
                taskItem.style.borderLeft = `4px solid ${taskColor}`;
                
                taskItem.innerHTML = `
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <div style="width: 16px; height: 16px; border-radius: 4px; background: ${taskColor}; border: 1px solid rgba(0,0,0,0.1);"></div>
                        <span class="task-name-large">${task.task_name}</span>
                    </div>
                    <span class="task-time-large">${formatTime(timeSeconds)}</span>
                `;
                
                breakdownContent.appendChild(taskItem);
            });
        }
        
        function changePeriod(period) {
            currentPeriod = period;
            
            // Update active button
            document.querySelectorAll('.period-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            document.getElementById(`period-${period}`).classList.add('active');
            
            // Automatically set calendar view based on period
            if (period === 'week') {
                setView('weekly');
            } else if (period === 'month') {
                setView('monthly');
            } else if (period === 'year') {
                setView('yearly');
            } else {
                setView('monthly');
            }
            
            loadStats();
        }
        
        function formatTime(seconds) {
            const hours = Math.floor(seconds / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            if (hours > 0) {
                return `${hours}h ${minutes}m`;
            }
            return `${minutes}m`;
        }
        
        function formatTimeShort(seconds) {
            const hours = Math.floor(seconds / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            if (hours > 0) {
                return `${hours}h`;
            }
            return `${minutes}m`;
        }
        
        function getWeekStart(date) {
            const d = new Date(date);
            const day = d.getDay();
            const diff = d.getDate() - day;
            return new Date(d.setDate(diff));
        }
        
        function getWeekEnd(date) {
            const weekStart = getWeekStart(date);
            const weekEnd = new Date(weekStart);
            weekEnd.setDate(weekEnd.getDate() + 6);
            return weekEnd;
        }
        
        function getMonthStart(date) {
            return new Date(date.getFullYear(), date.getMonth(), 1);
        }
        
        function getMonthEnd(date) {
            return new Date(date.getFullYear(), date.getMonth() + 1, 0);
        }
        
        function formatDateRange(start, end, view) {
            const options = { month: 'short', day: 'numeric', year: 'numeric' };
            if (view === 'daily') {
                return start.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
            } else if (view === 'weekly') {
                return `${start.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} - ${end.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`;
            } else if (view === 'yearly') {
                return start.toLocaleDateString('en-US', { year: 'numeric' });
            } else {
                return start.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
            }
        }
        
        function isToday(date) {
            const today = new Date();
            return date.toDateString() === today.toDateString();
        }
        
        function setView(view) {
            currentView = view;
            buildCalendar(allSessions);
        }
        
        function navigateCalendar(direction) {
            if (direction === 0) {
                currentDate = new Date();
            } else {
                if (currentView === 'daily') {
                    currentDate.setDate(currentDate.getDate() + direction);
                } else if (currentView === 'weekly') {
                    currentDate.setDate(currentDate.getDate() + (direction * 7));
                } else if (currentView === 'yearly') {
                    currentDate.setFullYear(currentDate.getFullYear() + direction);
                } else {
                    currentDate.setMonth(currentDate.getMonth() + direction);
                }
            }
            buildCalendar(allSessions);
        }
        
        function getLocalDateString(date) {
            // Format date as YYYY-MM-DD in local timezone
            const year = date.getFullYear();
            const month = String(date.getMonth() + 1).padStart(2, '0');
            const day = String(date.getDate()).padStart(2, '0');
            return `${year}-${month}-${day}`;
        }
        
        function getSessionsInRange(sessions, startDate, endDate) {
            return sessions.filter(session => {
                if (!session.start_time) return false;
                const sessionDate = new Date(session.start_time);
                return sessionDate >= startDate && sessionDate <= endDate;
            });
        }
        
        function getPeriodDateRange(period) {
            const today = new Date();
            today.setHours(0, 0, 0, 0);
            let startDate, endDate;
            
            if (period === 'week') {
                startDate = new Date(today);
                startDate.setDate(today.getDate() - 7);
                endDate = new Date(today);
                endDate.setHours(23, 59, 59, 999);
            } else if (period === 'month') {
                startDate = new Date(today);
                startDate.setDate(today.getDate() - 30);
                endDate = new Date(today);
                endDate.setHours(23, 59, 59, 999);
            } else if (period === 'year') {
                startDate = new Date(today);
                startDate.setDate(today.getDate() - 365);
                endDate = new Date(today);
                endDate.setHours(23, 59, 59, 999);
            } else { // 'all'
                startDate = null;
                endDate = null;
            }
            
            return { startDate, endDate };
        }
        
        function filterSessionsByPeriod(sessions, period) {
            if (period === 'all') {
                return sessions;
            }
            
            const { startDate, endDate } = getPeriodDateRange(period);
            if (!startDate || !endDate) {
                return sessions;
            }
            
            return sessions.filter(session => {
                if (!session.start_time) return false;
                const sessionDate = new Date(session.start_time);
                return sessionDate >= startDate && sessionDate <= endDate;
            });
        }
        
        function buildCalendar(sessions) {
            // Store all sessions (unfiltered)
            allSessions = sessions;
            const calendar = document.getElementById('calendar');
            calendar.innerHTML = '';
            calendar.className = `calendar ${currentView}-view`;
            // Reset display style for non-yearly views
            if (currentView !== 'yearly') {
                calendar.style.display = '';
                calendar.style.flexDirection = '';
                calendar.style.gap = '';
            }
            
            const daysOfWeek = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
            let startDate, endDate;
            const today = new Date();
            
            if (currentView === 'daily') {
                startDate = new Date(currentDate);
                startDate.setHours(0, 0, 0, 0);
                endDate = new Date(currentDate);
                endDate.setHours(23, 59, 59, 999);
            } else if (currentView === 'weekly') {
                startDate = getWeekStart(new Date(currentDate));
                startDate.setHours(0, 0, 0, 0);
                endDate = getWeekEnd(new Date(currentDate));
                endDate.setHours(23, 59, 59, 999);
            } else if (currentView === 'yearly') {
                // Yearly view - show entire year
                startDate = new Date(currentDate.getFullYear(), 0, 1);
                startDate.setHours(0, 0, 0, 0);
                endDate = new Date(currentDate.getFullYear(), 11, 31);
                endDate.setHours(23, 59, 59, 999);
            } else {
                // Monthly view
                startDate = getMonthStart(new Date(currentDate));
                startDate.setHours(0, 0, 0, 0);
                endDate = getMonthEnd(new Date(currentDate));
                endDate.setHours(23, 59, 59, 999);
            }
            
            // Update calendar title
            document.getElementById('calendar-title').textContent = formatDateRange(startDate, endDate, currentView);
            
            // Group sessions by date (using local date strings)
            const sessionsByDate = {};
            // Filter sessions for calendar display by calendar date range (show all sessions in the calendar view)
            const filteredSessions = getSessionsInRange(sessions, startDate, endDate);
            
            // Update Task Overview to show tasks ONLY from the calendar's current date range
            // (not filtered by period - we want to show tasks for exactly what's displayed in calendar)
            const calendarSessions = getSessionsInRange(sessions, startDate, endDate);
            displayTaskBreakdown(calendarSessions);
            
            filteredSessions.forEach(session => {
                if (session.start_time) {
                    // Convert session date to local date string
                    const sessionDate = new Date(session.start_time);
                    const date = getLocalDateString(sessionDate);
                    if (!sessionsByDate[date]) {
                        sessionsByDate[date] = { 
                            count: 0, 
                            totalTime: 0, 
                            tasks: new Set(), 
                            sessions: [],
                            tasksBreakdown: {} // Map of task_name -> total_time
                        };
                    }
                    sessionsByDate[date].count++;
                    const sessionTime = session.completed_seconds || 0;
                    sessionsByDate[date].totalTime += sessionTime;
                    sessionsByDate[date].tasks.add(session.task_name);
                    sessionsByDate[date].sessions.push(session);
                    
                    // Group by task name for breakdown
                    if (!sessionsByDate[date].tasksBreakdown[session.task_name]) {
                        sessionsByDate[date].tasksBreakdown[session.task_name] = 0;
                    }
                    sessionsByDate[date].tasksBreakdown[session.task_name] += sessionTime;
                }
            });
            
            // Build calendar days
            if (currentView === 'daily') {
                const dayDiv = createDayElement(currentDate, sessionsByDate, true);
                calendar.appendChild(dayDiv);
            } else if (currentView === 'weekly') {
                const weekStart = getWeekStart(new Date(currentDate));
                // Create header for weekly view
                daysOfWeek.forEach(day => {
                    const header = document.createElement('div');
                    header.className = 'calendar-header';
                    header.textContent = day;
                    calendar.appendChild(header);
                });
                for (let i = 0; i < 7; i++) {
                    const day = new Date(weekStart);
                    day.setDate(weekStart.getDate() + i);
                    const dayDiv = createDayElement(day, sessionsByDate, false);
                    calendar.appendChild(dayDiv);
                }
            } else if (currentView === 'yearly') {
                // Yearly view - show all 12 months in a grid
                const year = currentDate.getFullYear();
                const months = ['January', 'February', 'March', 'April', 'May', 'June', 
                               'July', 'August', 'September', 'October', 'November', 'December'];
                
                // Change calendar to a flex container for yearly view
                calendar.style.display = 'flex';
                calendar.style.flexDirection = 'column';
                calendar.style.gap = '30px';
                
                for (let monthIndex = 0; monthIndex < 12; monthIndex++) {
                    const monthContainer = document.createElement('div');
                    monthContainer.style.cssText = 'width: 100%;';
                    
                    const monthTitle = document.createElement('div');
                    monthTitle.style.cssText = 'font-weight: bold; font-size: 1.2em; margin-bottom: 10px; color: #667eea; text-align: center;';
                    monthTitle.textContent = months[monthIndex] + ' ' + year;
                    monthContainer.appendChild(monthTitle);
                    
                    // Create header for each month
                    const monthHeader = document.createElement('div');
                    monthHeader.style.cssText = 'display: grid; grid-template-columns: repeat(7, 1fr); gap: 10px; margin-bottom: 10px;';
                    daysOfWeek.forEach(day => {
                        const header = document.createElement('div');
                        header.className = 'calendar-header';
                        header.textContent = day;
                        monthHeader.appendChild(header);
                    });
                    monthContainer.appendChild(monthHeader);
                    
                    // Create month calendar grid
                    const monthGrid = document.createElement('div');
                    monthGrid.style.cssText = 'display: grid; grid-template-columns: repeat(7, 1fr); gap: 10px;';
                    
                    const monthStart = new Date(year, monthIndex, 1);
                    const monthEnd = new Date(year, monthIndex + 1, 0);
                    
                    // Fill empty cells before month start
                    const firstDayOfWeek = monthStart.getDay();
                    for (let i = 0; i < firstDayOfWeek; i++) {
                        const emptyDiv = document.createElement('div');
                        emptyDiv.className = 'calendar-day';
                        monthGrid.appendChild(emptyDiv);
                    }
                    
                    // Fill days of the month
                    const currentDay = new Date(monthStart);
                    while (currentDay <= monthEnd) {
                        const dayDiv = createDayElement(new Date(currentDay), sessionsByDate, false);
                        monthGrid.appendChild(dayDiv);
                        currentDay.setDate(currentDay.getDate() + 1);
                    }
                    
                    monthContainer.appendChild(monthGrid);
                    calendar.appendChild(monthContainer);
                }
            } else {
                // Monthly view
                // Create header for monthly view
                daysOfWeek.forEach(day => {
                    const header = document.createElement('div');
                    header.className = 'calendar-header';
                    header.textContent = day;
                    calendar.appendChild(header);
                });
                
                const monthStart = getMonthStart(new Date(currentDate));
                const monthEnd = getMonthEnd(new Date(currentDate));
                
                // Fill empty cells before month start
                const firstDayOfWeek = monthStart.getDay();
                for (let i = 0; i < firstDayOfWeek; i++) {
                    const emptyDiv = document.createElement('div');
                    emptyDiv.className = 'calendar-day';
                    calendar.appendChild(emptyDiv);
                }
                
                // Fill days of the month
                const currentDay = new Date(monthStart);
                while (currentDay <= monthEnd) {
                    const dayDiv = createDayElement(new Date(currentDay), sessionsByDate, false);
                    calendar.appendChild(dayDiv);
                    currentDay.setDate(currentDay.getDate() + 1);
                }
            }
        }
        
        function createDayElement(date, sessionsByDate, isDaily) {
            const dateStr = getLocalDateString(date);
            const dayData = sessionsByDate[dateStr];
            const today = isToday(date);
            
            const dayDiv = document.createElement('div');
            dayDiv.className = 'calendar-day' + 
                (isDaily ? ' daily-view' : ' clickable') +
                (dayData ? ' has-work' : '') +
                (today ? ' today' : '');
            
            // For non-daily views, add click handler to open session management modal
            if (!isDaily) {
                dayDiv.onclick = () => showDaySessions(date, dayData);
            }
            
            const dayNumber = document.createElement('div');
            dayNumber.className = 'day-number';
            dayNumber.textContent = date.getDate();
            dayDiv.appendChild(dayNumber);
            
            if (dayData) {
                // Keep day background neutral - only individual tasks will be colored
                // The has-work class will add the colored border via CSS
                
                const workTime = document.createElement('div');
                workTime.className = 'day-work-time';
                workTime.textContent = formatTimeShort(dayData.totalTime);
                workTime.style.fontWeight = 'bold';
                workTime.style.marginBottom = '8px';
                workTime.style.color = '#333';
                dayDiv.appendChild(workTime);
                
                // Show task breakdown for non-daily views
                if (!isDaily && dayData.tasksBreakdown) {
                    const tasksList = document.createElement('div');
                    tasksList.className = 'day-tasks-breakdown';
                    tasksList.style.fontSize = '0.75em';
                    tasksList.style.lineHeight = '1.4';
                    tasksList.style.maxHeight = '120px';
                    tasksList.style.overflowY = 'auto';
                    tasksList.style.padding = '4px 0';
                    
                    // Sort tasks by time (descending)
                    const sortedTasksArray = Object.entries(dayData.tasksBreakdown)
                        .sort((a, b) => b[1] - a[1]);
                    
                    sortedTasksArray.forEach(([taskName, taskTime]) => {
                        const taskColor = getTaskColor(taskName);
                        const taskItem = document.createElement('div');
                        taskItem.style.marginBottom = '4px';
                        taskItem.style.padding = '4px 6px';
                        taskItem.style.borderBottom = '1px solid #e0e0e0';
                        taskItem.style.borderLeft = `3px solid ${taskColor}`;
                        taskItem.style.borderRadius = '4px';
                        taskItem.style.background = '#f9f9f9';
                        taskItem.style.display = 'flex';
                        taskItem.style.alignItems = 'center';
                        taskItem.style.gap = '8px';
                        taskItem.innerHTML = `
                            <div style="width: 12px; height: 12px; border-radius: 3px; background: ${taskColor}; border: 1px solid rgba(0,0,0,0.1); flex-shrink: 0;"></div>
                            <div style="flex: 1;">
                                <div style="font-weight: 600; color: #333; margin-bottom: 2px;">
                                    ${taskName}
                                </div>
                                <div style="color: #666; font-size: 0.9em;">
                                    ${formatTimeShort(taskTime)}
                                </div>
                            </div>
                        `;
                        tasksList.appendChild(taskItem);
                    });
                    
                    dayDiv.appendChild(tasksList);
                }
                
                // In daily view, show full session list with edit/delete buttons
                if (isDaily && dayData.sessions) {
                    const sessionsDiv = document.createElement('div');
                    sessionsDiv.className = 'day-sessions';
                    sessionsDiv.style.marginTop = '15px';
                    
                    // Add "Add Session" button at the top
                    const addBtn = document.createElement('button');
                    addBtn.className = 'btn btn-add';
                    addBtn.textContent = '+ Add Session';
                    addBtn.style.width = '100%';
                    addBtn.style.marginBottom = '15px';
                    addBtn.onclick = (e) => {
                        e.stopPropagation();
                        addSessionForDate(dateStr);
                    };
                    sessionsDiv.appendChild(addBtn);
                    
                    // Add sessions list
                    dayData.sessions.forEach(session => {
                        const sessionDiv = document.createElement('div');
                        sessionDiv.className = 'session-item';
                        sessionDiv.style.marginBottom = '10px';
                        sessionDiv.style.padding = '12px';
                        sessionDiv.style.background = 'rgba(255,255,255,0.15)';
                        sessionDiv.style.borderRadius = '6px';
                        sessionDiv.style.display = 'flex';
                        sessionDiv.style.justifyContent = 'space-between';
                        sessionDiv.style.alignItems = 'center';
                        
                        const sessionInfo = document.createElement('div');
                        sessionInfo.style.flex = '1';
                        const startTime = new Date(session.start_time);
                        const endTime = session.end_time ? new Date(session.end_time) : null;
                        const duration = formatTime(session.completed_seconds || 0);
                        
                        sessionInfo.innerHTML = `
                            <div style="font-weight: bold; margin-bottom: 5px; color: white;">
                                ${session.task_name}
                            </div>
                            <div style="color: rgba(255,255,255,0.9); font-size: 0.9em;">
                                ${startTime.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })} ${endTime ? '- ' + endTime.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }) : ''}
                                <br>
                                Duration: ${duration}
                            </div>
                        `;
                        
                        const sessionActions = document.createElement('div');
                        sessionActions.style.display = 'flex';
                        sessionActions.style.gap = '8px';
                        
                        const editBtn = document.createElement('button');
                        editBtn.className = 'btn btn-edit';
                        editBtn.textContent = 'Edit';
                        editBtn.onclick = (e) => {
                            e.stopPropagation();
                            editSession(session.id);
                        };
                        
                        const deleteBtn = document.createElement('button');
                        deleteBtn.className = 'btn btn-delete';
                        deleteBtn.textContent = 'Delete';
                        deleteBtn.onclick = (e) => {
                            e.stopPropagation();
                            deleteSession(session.id);
                        };
                        
                        sessionActions.appendChild(editBtn);
                        sessionActions.appendChild(deleteBtn);
                        
                        sessionDiv.appendChild(sessionInfo);
                        sessionDiv.appendChild(sessionActions);
                        sessionsDiv.appendChild(sessionDiv);
                    });
                    dayDiv.appendChild(sessionsDiv);
                }
                
                if (!isDaily) {
                    // Create detailed tooltip with task breakdown
                    const taskDetails = Object.entries(dayData.tasksBreakdown || {})
                        .map(([task, time]) => `${task}: ${formatTimeShort(time)}`)
                        .join('; ');
                    dayDiv.title = `${dateStr}: ${dayData.count} session(s), Total: ${formatTime(dayData.totalTime)}. ${taskDetails}. Click to manage sessions.`;
                }
            } else {
                if (isDaily) {
                    // In daily view, show "Add Session" button even when no sessions
                    const addBtn = document.createElement('button');
                    addBtn.className = 'btn btn-add';
                    addBtn.textContent = '+ Add Session';
                    addBtn.style.width = '100%';
                    addBtn.style.marginTop = '15px';
                    addBtn.onclick = (e) => {
                        e.stopPropagation();
                        addSessionForDate(dateStr);
                    };
                    dayDiv.appendChild(addBtn);
                } else {
                    dayDiv.title = `${dateStr}. Click to add a session.`;
                }
            }
            
            return dayDiv;
        }
        
        function showDaySessions(date, dayData) {
            const dateStr = getLocalDateString(date);
            const sessionsForDay = allSessions.filter(s => {
                if (!s.start_time) return false;
                const sessionDate = new Date(s.start_time);
                return getLocalDateString(sessionDate) === dateStr;
            });
            
            // Create modal content for day sessions
            const modal = document.getElementById('day-sessions-modal');
            const modalContent = document.getElementById('day-sessions-content');
            
            let html = `
                <div class="modal-header">
                    <h2>Sessions for ${date.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })}</h2>
                    <button class="close-btn" onclick="closeDaySessionsModal()">&times;</button>
                </div>
                <div style="padding: 20px 0;">
                    <button class="btn btn-add" onclick="addSessionForDate('${dateStr}'); closeDaySessionsModal();" style="width: 100%; margin-bottom: 20px;">+ Add Session</button>
            `;
            
            if (sessionsForDay.length > 0) {
                html += '<ul class="sessions-list" style="list-style: none; padding: 0;">';
                sessionsForDay.forEach(session => {
                    const startDate = new Date(session.start_time);
                    const endDate = session.end_time ? new Date(session.end_time) : null;
                    const duration = formatTime(session.completed_seconds || 0);
                    
                    html += `
                        <li class="session-item" style="margin-bottom: 10px;">
                            <div class="session-info">
                                <div style="font-weight: bold; margin-bottom: 5px;">
                                    ${session.task_name}
                                </div>
                                <div style="color: #666; font-size: 0.9em;">
                                    ${startDate.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })} ${endDate ? '- ' + endDate.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }) : ''}
                                    <br>
                                    Duration: ${duration}
                                </div>
                            </div>
                            <div class="session-actions">
                                <button class="btn btn-edit" onclick="editSession(${session.id}); closeDaySessionsModal();">Edit</button>
                                <button class="btn btn-delete" onclick="deleteSession(${session.id}); closeDaySessionsModal();">Delete</button>
                            </div>
                        </li>
                    `;
                });
                html += '</ul>';
            } else {
                html += '<p style="text-align: center; color: #999; padding: 20px;">No sessions for this day.</p>';
            }
            
            html += '</div>';
            modalContent.innerHTML = html;
            modal.style.display = 'flex';
        }
        
        function addSessionForDate(dateStr) {
            document.getElementById('modal-title').textContent = 'Add Session';
            document.getElementById('session-id').value = '';
            document.getElementById('session-form').reset();
            
            // Set default values with the selected date
            const date = new Date(dateStr);
            const now = new Date();
            const localDateTime = new Date(date.getFullYear(), date.getMonth(), date.getDate(), now.getHours(), now.getMinutes());
            const localDateTimeStr = new Date(localDateTime.getTime() - localDateTime.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
            
            document.getElementById('start-time').value = localDateTimeStr;
            const endDateTime = new Date(localDateTime.getTime() + 25 * 60000);
            const endDateTimeStr = new Date(endDateTime.getTime() - endDateTime.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
            document.getElementById('end-time').value = endDateTimeStr;
            document.getElementById('duration').value = 25;
            document.getElementById('completed-time').value = 25;
            document.getElementById('status').value = 'completed';
            
            document.getElementById('session-modal').style.display = 'flex';
        }
        
        function closeDaySessionsModal() {
            document.getElementById('day-sessions-modal').style.display = 'none';
        }
        
        
        function exportICalendar() {
            fetch('/api/export/ical')
                .then(response => response.blob())
                .then(blob => {
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `pomodoro-sessions-${new Date().toISOString().split('T')[0]}.ics`;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    document.body.removeChild(a);
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('Error exporting calendar');
                });
        }
        
        // URL Routing System
        function updateURL(path, replace = false) {
            const newURL = '#' + path;
            if (replace) {
                window.history.replaceState({path: path}, '', newURL);
            } else {
                window.history.pushState({path: path}, '', newURL);
            }
        }
        
        function parseRoute() {
            const hash = window.location.hash.slice(1) || '/stats';
            const parts = hash.split('/').filter(p => p);
            return {
                view: parts[0] || 'stats',
                section: parts[1] || null,
                step: parts[2] || null,
                fullPath: hash
            };
        }
        
        function route() {
            const route = parseRoute();
            
            // Handle main views
            if (route.view === 'stats') {
                showView('stats', false);
            } else if (route.view === 'settings') {
                showView('settings', false);
                
                // Handle calendar setup wizard routing
                if (route.section === 'calendar-setup') {
                    // Ensure wizard is visible
                    document.getElementById('caldav-wizard').style.display = 'block';
                    document.getElementById('caldav-current-connection').style.display = 'none';
                    document.getElementById('caldav-start-setup').style.display = 'none';
                    
                    if (route.step) {
                        // Extract step number from step-1, step-2, etc.
                        const stepMatch = route.step.match(/step-(\d+)/);
                        if (stepMatch) {
                            const stepNum = parseInt(stepMatch[1]);
                            if (stepNum >= 1 && stepNum <= 4) {
                                // Show the step (without updating history since we're routing)
                                showWizardStep(stepNum, false);
                                // Restore provider selection visual state if on step 1
                                if (stepNum === 1 && wizardSelectedProvider) {
                                    const providerElement = document.getElementById(`provider-${wizardSelectedProvider}`);
                                    if (providerElement) {
                                        providerElement.classList.add('selected');
                                        providerElement.style.borderColor = '#007bff';
                                        providerElement.style.background = 'rgba(0,123,255,0.1)';
                                        document.getElementById('wizard-step-1-next').disabled = false;
                                        document.getElementById('wizard-step-1-next').style.opacity = '1';
                                        document.getElementById('wizard-step-1-next').style.cursor = 'pointer';
                                    }
                                }
                                // If on step 2+, ensure provider instructions are set up
                                if (stepNum >= 2 && wizardSelectedProvider) {
                                    setupProviderInstructions();
                                }
                            }
                        } else if (route.step === 'start') {
                            // Start wizard
                            startCalDAVWizard();
                        }
                    } else {
                        // Just /settings/calendar-setup - start wizard
                        startCalDAVWizard();
                    }
                } else {
                    // Regular settings view - hide wizard
                    document.getElementById('caldav-wizard').style.display = 'none';
                    loadCalDAVConfig();
                }
            }
        }
        
        // Listen for browser back/forward
        window.addEventListener('popstate', function(event) {
            route();
        });
        
        function showView(view, updateHistory = true) {
            // Hide all views
            const statsView = document.getElementById('stats-view');
            const settingsView = document.getElementById('settings-view');
            if (statsView) statsView.style.display = 'none';
            if (settingsView) settingsView.style.display = 'none';
            
            // Remove active class from all tabs
            document.querySelectorAll('.nav-tab').forEach(tab => {
                tab.classList.remove('active');
            });
            
            // Show selected view
            if (view === 'stats') {
                if (statsView) statsView.style.display = 'block';
                const navStats = document.getElementById('nav-stats');
                if (navStats) navStats.classList.add('active');
                if (updateHistory) {
                    updateURL('/stats');
                }
            } else if (view === 'settings') {
                if (settingsView) settingsView.style.display = 'block';
                const navSettings = document.getElementById('nav-settings');
                if (navSettings) navSettings.classList.add('active');
                if (updateHistory) {
                    updateURL('/settings');
                }
                loadCalDAVConfig();
            }
        }
        
        function showSyncModal() {
            document.getElementById('sync-modal').style.display = 'flex';
            loadCalDAVConfig();
        }
        
        function closeSyncModal() {
            document.getElementById('sync-modal').style.display = 'none';
        }
        
        // Wizard state
        let wizardCurrentStep = 1;
        let wizardSelectedProvider = null;
        let wizardTestPassed = false;
        let googleUrlUpdateHandler = null;
        
        function loadCalDAVConfig() {
            fetch('/api/caldav/config')
                .then(response => response.json())
                .then(data => {
                    if (data.success && data.config.url) {
                        // Show current connection info
                        const connectionInfo = document.getElementById('caldav-connection-info');
                        const providerName = getProviderName(data.config.url);
                        connectionInfo.innerHTML = `
                            <div style="margin-bottom: 10px;"><strong>Provider:</strong> ${providerName}</div>
                            <div style="margin-bottom: 10px;"><strong>Server URL:</strong> ${data.config.url}</div>
                            <div style="margin-bottom: 10px;"><strong>Username:</strong> ${data.config.username || 'Not set'}</div>
                            <div><strong>Calendar Name:</strong> ${data.config.calendar_name || 'Pomodoro Sessions'}</div>
                        `;
                        document.getElementById('caldav-current-connection').style.display = 'block';
                        document.getElementById('caldav-wizard').style.display = 'none';
                        document.getElementById('caldav-start-setup').style.display = 'none';
                    } else {
                        // Show start setup button
                        document.getElementById('caldav-current-connection').style.display = 'none';
                        document.getElementById('caldav-wizard').style.display = 'none';
                        document.getElementById('caldav-start-setup').style.display = 'block';
                    }
                })
                .catch(error => {
                    console.error('Error loading CalDAV config:', error);
                });
        }
        
        function getProviderName(url) {
            if (!url) return 'Unknown';
            if (url.includes('icloud.com')) return 'iCloud';
            if (url.includes('googleusercontent.com') || url.includes('google.com')) return 'Google Calendar';
            return 'Custom CalDAV';
        }
        
        function startCalDAVWizard() {
            // Reset wizard state
            wizardCurrentStep = 1;
            wizardSelectedProvider = null;
            wizardTestPassed = false;
            
            // Clear Google URL update handler
            const usernameInput = document.getElementById('wizard-caldav-username');
            if (googleUrlUpdateHandler) {
                usernameInput.removeEventListener('input', googleUrlUpdateHandler);
                googleUrlUpdateHandler = null;
            }
            
            // Reset form fields
            document.getElementById('wizard-caldav-url').value = '';
            document.getElementById('wizard-caldav-username').value = '';
            document.getElementById('wizard-caldav-password').value = '';
            document.getElementById('wizard-caldav-calendar-name').value = 'Pomodoro Sessions';
            document.getElementById('wizard-caldav-url').readOnly = false;
            
            // Reset provider selection
            document.querySelectorAll('.provider-option').forEach(el => {
                el.classList.remove('selected');
                el.style.borderColor = '#ddd';
                el.style.background = 'transparent';
            });
            
            // Reset test status
            document.getElementById('wizard-test-status').style.display = 'none';
            document.getElementById('wizard-step-1-next').disabled = true;
            document.getElementById('wizard-step-1-next').style.opacity = '0.5';
            document.getElementById('wizard-step-1-next').style.cursor = 'not-allowed';
            document.getElementById('wizard-step-3-next').disabled = true;
            document.getElementById('wizard-step-3-next').style.opacity = '0.5';
            document.getElementById('wizard-step-3-next').style.cursor = 'not-allowed';
            
            // Hide instruction divs
            document.getElementById('wizard-icloud-instructions').style.display = 'none';
            document.getElementById('wizard-google-instructions').style.display = 'none';
            document.getElementById('wizard-other-instructions').style.display = 'none';
            
            // Hide current connection and start button
            document.getElementById('caldav-current-connection').style.display = 'none';
            document.getElementById('caldav-start-setup').style.display = 'none';
            
            // Show wizard and reset to step 1
            document.getElementById('caldav-wizard').style.display = 'block';
            showWizardStep(1);
        }
        
        function cancelCalDAVWizard() {
            document.getElementById('caldav-wizard').style.display = 'none';
            loadCalDAVConfig(); // Reload to show appropriate state
            // Navigate back to settings page
            updateURL('/settings');
        }
        
        function showWizardStep(step, updateHistory = true) {
            // Hide all steps
            for (let i = 1; i <= 4; i++) {
                document.getElementById(`wizard-step-${i}`).style.display = 'none';
                const indicator = document.getElementById(`wizard-step-${i}-indicator`);
                if (i <= step) {
                    indicator.style.background = '#007bff';
                    indicator.style.color = 'white';
                    indicator.style.boxShadow = '0 0 0 2px #007bff';
                } else {
                    indicator.style.background = '#ddd';
                    indicator.style.color = '#666';
                    indicator.style.boxShadow = '0 0 0 2px #ddd';
                }
            }
            
            // Show current step
            document.getElementById(`wizard-step-${step}`).style.display = 'block';
            wizardCurrentStep = step;
            
            // Update URL if not called from route handler
            if (updateHistory) {
                updateURL(`/settings/calendar-setup/step-${step}`);
            }
        }
        
        function selectProvider(provider, element) {
            wizardSelectedProvider = provider;
            
            // Highlight selected provider
            document.querySelectorAll('.provider-option').forEach(el => {
                el.classList.remove('selected');
                el.style.borderColor = '#ddd';
                el.style.background = 'transparent';
            });
            element.classList.add('selected');
            element.style.borderColor = '#007bff';
            element.style.background = 'rgba(0,123,255,0.1)';
            
            // Enable next button
            document.getElementById('wizard-step-1-next').disabled = false;
            document.getElementById('wizard-step-1-next').style.opacity = '1';
            document.getElementById('wizard-step-1-next').style.cursor = 'pointer';
        }
        
        function wizardNextStep() {
            if (wizardCurrentStep === 1) {
                if (!wizardSelectedProvider) {
                    alert('Please select a calendar provider');
                    return;
                }
                showWizardStep(2);
                setupProviderInstructions();
            } else if (wizardCurrentStep === 2) {
                // Validate inputs
                const url = document.getElementById('wizard-caldav-url').value.trim();
                const username = document.getElementById('wizard-caldav-username').value.trim();
                const password = document.getElementById('wizard-caldav-password').value.trim();
                
                if (!url || !username || !password) {
                    alert('Please fill in all required fields');
                    return;
                }
                showWizardStep(3);
                wizardTestPassed = false;
                document.getElementById('wizard-step-3-next').disabled = true;
                document.getElementById('wizard-step-3-next').style.opacity = '0.5';
                document.getElementById('wizard-step-3-next').style.cursor = 'not-allowed';
            } else if (wizardCurrentStep === 3) {
                if (!wizardTestPassed) {
                    alert('Please test the connection first');
                    return;
                }
                // Save configuration
                saveWizardConfig();
            }
        }
        
        function wizardPreviousStep() {
            if (wizardCurrentStep > 1) {
                const previousStep = wizardCurrentStep - 1;
                showWizardStep(previousStep);
                // If going back to step 2, restore provider instructions
                if (previousStep === 2 && wizardSelectedProvider) {
                    setupProviderInstructions();
                }
            }
        }
        
        function setupProviderInstructions() {
            // Hide all instruction divs
            document.getElementById('wizard-icloud-instructions').style.display = 'none';
            document.getElementById('wizard-google-instructions').style.display = 'none';
            document.getElementById('wizard-other-instructions').style.display = 'none';
            
            // Show relevant instructions and set defaults
            const urlInput = document.getElementById('wizard-caldav-url');
            const usernameInput = document.getElementById('wizard-caldav-username');
            
            if (wizardSelectedProvider === 'icloud') {
                document.getElementById('wizard-icloud-instructions').style.display = 'block';
                urlInput.value = 'https://caldav.icloud.com';
                urlInput.placeholder = 'https://caldav.icloud.com';
                urlInput.readOnly = false;
            } else if (wizardSelectedProvider === 'google') {
                document.getElementById('wizard-google-instructions').style.display = 'block';
                urlInput.placeholder = 'Will be set automatically after entering email';
                urlInput.readOnly = true;
                // Update URL when username changes
                // Remove old listener if it exists
                if (googleUrlUpdateHandler) {
                    usernameInput.removeEventListener('input', googleUrlUpdateHandler);
                }
                googleUrlUpdateHandler = function() {
                    const email = usernameInput.value.trim();
                    if (email && email.includes('@')) {
                        urlInput.value = `https://apidata.googleusercontent.com/caldav/v2/${email}/events/`;
                    } else {
                        urlInput.value = '';
                    }
                };
                usernameInput.addEventListener('input', googleUrlUpdateHandler);
                // Update URL if username already has a value
                googleUrlUpdateHandler();
            } else {
                document.getElementById('wizard-other-instructions').style.display = 'block';
                urlInput.placeholder = 'https://your-caldav-server.com';
                urlInput.readOnly = false;
            }
        }
        
        function wizardTestConnection() {
            const url = document.getElementById('wizard-caldav-url').value.trim();
            const username = document.getElementById('wizard-caldav-username').value.trim();
            const password = document.getElementById('wizard-caldav-password').value.trim();
            const calendarName = document.getElementById('wizard-caldav-calendar-name').value.trim() || 'Pomodoro Sessions';
            
            if (!url || !username || !password) {
                alert('Please fill in all required fields');
                return;
            }
            
            const statusDiv = document.getElementById('wizard-test-status');
            const statusText = document.getElementById('wizard-test-status-text');
            const testBtn = document.getElementById('wizard-test-btn');
            
            statusDiv.style.display = 'block';
            statusText.textContent = 'Testing connection...';
            statusDiv.style.background = 'rgba(0,123,255,0.1)';
            statusText.style.color = '#007bff';
            testBtn.disabled = true;
            
            fetch('/api/caldav/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url, username, password, calendar_name: calendarName})
            })
            .then(response => response.json())
            .then(data => {
                testBtn.disabled = false;
                if (data.success) {
                    statusText.textContent = '✓ Connection successful! You can proceed to save the configuration.';
                    statusDiv.style.background = 'rgba(40,167,69,0.1)';
                    statusText.style.color = '#28a745';
                    wizardTestPassed = true;
                    document.getElementById('wizard-step-3-next').disabled = false;
                    document.getElementById('wizard-step-3-next').style.opacity = '1';
                    document.getElementById('wizard-step-3-next').style.cursor = 'pointer';
                } else {
                    statusText.textContent = '✗ Connection failed: ' + (data.error || 'Unknown error');
                    statusDiv.style.background = 'rgba(220,53,69,0.1)';
                    statusText.style.color = '#dc3545';
                    wizardTestPassed = false;
                }
            })
            .catch(error => {
                testBtn.disabled = false;
                statusText.textContent = '✗ Error: ' + error.message;
                statusDiv.style.background = 'rgba(220,53,69,0.1)';
                statusText.style.color = '#dc3545';
                wizardTestPassed = false;
            });
        }
        
        function saveWizardConfig() {
            const url = document.getElementById('wizard-caldav-url').value.trim();
            const username = document.getElementById('wizard-caldav-username').value.trim();
            const password = document.getElementById('wizard-caldav-password').value.trim();
            const calendarName = document.getElementById('wizard-caldav-calendar-name').value.trim() || 'Pomodoro Sessions';
            
            fetch('/api/caldav/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url, username, password, calendar_name: calendarName})
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showWizardStep(4);
                } else {
                    alert('Error saving configuration: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(error => {
                alert('Error: ' + error.message);
            });
        }
        
        function wizardComplete() {
            document.getElementById('caldav-wizard').style.display = 'none';
            loadCalDAVConfig(); // Reload to show connection info
            // Navigate back to settings page
            updateURL('/settings');
        }
        
        function saveCalDAVConfig() {
            const url = document.getElementById('caldav-url').value.trim();
            const username = document.getElementById('caldav-username').value.trim();
            const password = document.getElementById('caldav-password').value.trim();
            const calendarName = document.getElementById('caldav-calendar-name').value.trim() || 'Pomodoro Sessions';
            
            if (!url || !username || !password) {
                alert('Please fill in all required fields');
                return;
            }
            
            showCalDAVStatus('Saving configuration...', 'info');
            
            fetch('/api/caldav/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url, username, password, calendar_name: calendarName})
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showCalDAVStatus('Configuration saved and connected!', 'success');
                    document.getElementById('caldav-config-form').style.display = 'none';
                    document.getElementById('caldav-sync-controls').style.display = 'block';
                } else {
                    showCalDAVStatus('Error: ' + (data.error || 'Failed to connect'), 'error');
                }
            })
            .catch(error => {
                showCalDAVStatus('Error: ' + error.message, 'error');
            });
        }
        
        function testCalDAVConnection() {
            const url = document.getElementById('caldav-url').value.trim();
            const username = document.getElementById('caldav-username').value.trim();
            const password = document.getElementById('caldav-password').value.trim();
            const calendarName = document.getElementById('caldav-calendar-name').value.trim() || 'Pomodoro Sessions';
            
            if (!url || !username || !password) {
                alert('Please fill in all required fields');
                return;
            }
            
            showCalDAVStatus('Testing connection...', 'info');
            
            fetch('/api/caldav/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url, username, password, calendar_name: calendarName})
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showCalDAVStatus('Connection successful!', 'success');
                } else {
                    showCalDAVStatus('Connection failed: ' + (data.error || 'Unknown error'), 'error');
                }
            })
            .catch(error => {
                showCalDAVStatus('Error: ' + error.message, 'error');
            });
        }
        
        function syncCalDAV() {
            showCalDAVStatus('Syncing...', 'info');
            fetch('/api/caldav/sync', {method: 'POST'})
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        const to = data.to_calendar || {};
                        const from = data.from_calendar || {};
                        let msg = `Sync complete! Pushed: ${to.synced || 0} new, ${to.updated || 0} updated`;
                        if (to.deleted > 0) {
                            msg += `, deleted ${to.deleted} removed events`;
                        }
                        msg += `. Pulled: ${from.imported || 0} new, ${from.updated || 0} updated.`;
                        showCalDAVStatus(msg, 'success');
                        loadStats();
                    } else {
                        showCalDAVStatus('Sync failed: ' + (data.error || 'Unknown error'), 'error');
                    }
                })
                .catch(error => {
                    showCalDAVStatus('Error: ' + error.message, 'error');
                });
        }
        
        function syncCalDAVTo() {
            showCalDAVStatus('Pushing to calendar...', 'info');
            fetch('/api/caldav/sync/to', {method: 'POST'})
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        let message = data.message || `Pushed ${data.synced || 0} new and ${data.updated || 0} updated events`;
                        if (data.deleted > 0) {
                            message += `, deleted ${data.deleted} removed events`;
                        }
                        showCalDAVStatus(message, 'success');
                        loadStats();
                    } else {
                        showCalDAVStatus('Error: ' + (data.error || 'Unknown error'), 'error');
                    }
                })
                .catch(error => {
                    showCalDAVStatus('Error: ' + error.message, 'error');
                });
        }
        
        function syncCalDAVFrom() {
            showCalDAVStatus('Pulling from calendar...', 'info');
            fetch('/api/caldav/sync/from', {method: 'POST'})
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        let message = data.message || `Pulled ${data.imported || 0} new and ${data.updated || 0} updated events`;
                        if (data.total_events !== undefined) {
                            message = `Found ${data.total_events} calendar events. ${message}`;
                        }
                        showCalDAVStatus(message, 'success');
                        loadStats();
                    } else {
                        showCalDAVStatus('Error: ' + (data.error || 'Unknown error'), 'error');
                    }
                })
                .catch(error => {
                    showCalDAVStatus('Error: ' + error.message, 'error');
                });
        }
        
        function clearCalDAVConfig() {
            if (confirm('Are you sure you want to clear the CalDAV configuration? This will disconnect your calendar sync.')) {
                // Clear config on server
                fetch('/api/caldav/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({url: '', username: '', password: '', calendar_name: ''})
                })
                .then(response => response.json())
                .then(data => {
                    // Reload config to show start setup button
                    loadCalDAVConfig();
                    showCalDAVStatus('Configuration cleared', 'success');
                })
                .catch(error => {
                    console.error('Error clearing config:', error);
                    showCalDAVStatus('Error clearing configuration', 'error');
                });
            }
        }
        
        function showCalDAVStatus(message, type) {
            const statusDiv = document.getElementById('caldav-status');
            const statusText = document.getElementById('caldav-status-text');
            statusDiv.style.display = 'block';
            statusText.textContent = message;
            statusDiv.style.background = type === 'success' ? 'rgba(40,167,69,0.1)' : 
                                         type === 'error' ? 'rgba(220,53,69,0.1)' : 
                                         'rgba(0,123,255,0.1)';
            statusText.style.color = type === 'success' ? '#28a745' : 
                                     type === 'error' ? '#dc3545' : 
                                     '#007bff';
        }
        
        function importICalendar() {
            const fileInput = document.getElementById('ical-file');
            const file = fileInput.files[0];
            if (!file) {
                alert('Please select a file');
                return;
            }
            
            const formData = new FormData();
            formData.append('file', file);
            
            fetch('/api/import/ical', {
                method: 'POST',
                body: formData
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        alert(`Successfully imported ${data.imported} sessions`);
                        closeSyncModal();
                        loadStats();
                    } else {
                        alert('Error importing calendar: ' + (data.error || 'Unknown error'));
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('Error importing calendar');
                });
        }
        
        // Close sync modal when clicking outside
        window.onclick = function(event) {
            const modal = document.getElementById('sync-modal');
            if (event.target === modal) {
                closeSyncModal();
            }
        }
        
        function displayStats(data) {
            const stats = data.stats;
            const sessions = data.sessions;
            
            // Build calendar (will update Task Overview and calendar based on view)
            buildCalendar(sessions);
            
            // Show content
            document.getElementById('loading').style.display = 'none';
            const navTabs = document.getElementById('nav-tabs');
            if (navTabs) {
                navTabs.style.display = 'flex';
            }
            // Initialize routing - if no hash, default to stats
            if (!window.location.hash) {
                updateURL('/stats', true);
            }
            route(); // Route based on current URL
        }
        
        function editSession(sessionId) {
            fetch('/api/stats')
                .then(response => response.json())
                .then(data => {
                    const session = data.sessions.find(s => s.id === sessionId);
                    if (!session) return;
                    
                    document.getElementById('modal-title').textContent = 'Edit Session';
                    document.getElementById('session-id').value = session.id;
                    document.getElementById('task-name').value = session.task_name;
                    
                    const startDate = new Date(session.start_time);
                    const endDate = session.end_time ? new Date(session.end_time) : null;
                    const startLocal = new Date(startDate.getTime() - startDate.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
                    const endLocal = endDate ? new Date(endDate.getTime() - endDate.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : '';
                    
                    document.getElementById('start-time').value = startLocal;
                    document.getElementById('end-time').value = endLocal;
                    document.getElementById('duration').value = Math.floor(session.duration_seconds / 60);
                    document.getElementById('completed-time').value = Math.floor((session.completed_seconds || 0) / 60);
                    document.getElementById('status').value = session.status;
                    
                    document.getElementById('session-modal').style.display = 'flex';
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('Error loading session');
                });
        }
        
        function deleteSession(sessionId) {
            if (!confirm('Are you sure you want to delete this session?')) {
                return;
            }
            
            fetch(`/api/sessions/${sessionId}`, {
                method: 'DELETE'
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        loadStats();
                    } else {
                        alert('Error deleting session: ' + (data.error || 'Unknown error'));
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('Error deleting session');
                });
        }
        
        function saveSession(event) {
            event.preventDefault();
            
            const sessionId = document.getElementById('session-id').value;
            const taskName = document.getElementById('task-name').value;
            const startTime = document.getElementById('start-time').value;
            const endTime = document.getElementById('end-time').value;
            const duration = parseInt(document.getElementById('duration').value) * 60;
            const completedTime = parseInt(document.getElementById('completed-time').value) * 60;
            const status = document.getElementById('status').value;
            
            const sessionData = {
                task_name: taskName,
                duration_seconds: duration,
                start_time: new Date(startTime).toISOString(),
                end_time: endTime ? new Date(endTime).toISOString() : null,
                status: status,
                completed_seconds: completedTime
            };
            
            const url = sessionId ? `/api/sessions/${sessionId}` : '/api/sessions';
            const method = sessionId ? 'PUT' : 'POST';
            
            fetch(url, {
                method: method,
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(sessionData)
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        closeModal();
                        loadStats();
                    } else {
                        alert('Error saving session: ' + (data.error || 'Unknown error'));
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('Error saving session');
                });
        }
        
        function closeModal() {
            document.getElementById('session-modal').style.display = 'none';
        }
        
        // Close modals when clicking outside
        window.onclick = function(event) {
            const sessionModal = document.getElementById('session-modal');
            const syncModal = document.getElementById('sync-modal');
            const daySessionsModal = document.getElementById('day-sessions-modal');
            const colorsModal = document.getElementById('colors-modal');
            if (event.target === sessionModal) {
                closeModal();
            }
            if (event.target === syncModal) {
                closeSyncModal();
            }
            if (event.target === daySessionsModal) {
                closeDaySessionsModal();
            }
            if (event.target === colorsModal) {
                closeColorsModal();
            }
        }
        
        function showColorsModal() {
            // Get all unique task names from sessions
            const taskNames = [...new Set(allSessions.map(s => s.task_name).filter(Boolean))];
            
            // Ensure all tasks have colors loaded
            taskNames.forEach(taskName => {
                if (!taskColors[taskName]) {
                    // Request color from backend
                    fetch(`/api/task-colors`)
                        .then(response => response.json())
                        .then(colors => {
                            Object.assign(taskColors, colors);
                            renderColorsList();
                        });
                }
            });
            
            renderColorsList();
            document.getElementById('colors-modal').style.display = 'flex';
        }
        
        function renderColorsList() {
            const colorsList = document.getElementById('colors-list');
            const taskNames = [...new Set(allSessions.map(s => s.task_name).filter(Boolean))].sort();
            
            if (taskNames.length === 0) {
                colorsList.innerHTML = '<p style="text-align: center; color: #999; padding: 20px;">No tasks found. Create some sessions first!</p>';
                return;
            }
            
            colorsList.innerHTML = '';
            
            taskNames.forEach(taskName => {
                const color = getTaskColor(taskName);
                const colorItem = document.createElement('div');
                colorItem.className = 'color-item';
                colorItem.innerHTML = `
                    <div class="color-item-info">
                        <div class="color-preview" style="background: ${color};" onclick="document.getElementById('color-picker-${taskName.replace(/[^a-zA-Z0-9]/g, '-')}').click();"></div>
                        <span class="task-name-color">${taskName}</span>
                    </div>
                    <input type="color" id="color-picker-${taskName.replace(/[^a-zA-Z0-9]/g, '-')}" 
                           value="${color}" 
                           class="color-picker-input"
                           onchange="updateTaskColor('${taskName.replace(/'/g, "\\'")}', this.value)">
                `;
                colorsList.appendChild(colorItem);
            });
        }
        
        function updateTaskColor(taskName, color) {
            fetch(`/api/task-colors/${encodeURIComponent(taskName)}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ color: color })
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        taskColors[taskName] = color;
                        // Update preview
                        const preview = document.querySelector(`#color-picker-${taskName.replace(/[^a-zA-Z0-9]/g, '-')}`).previousElementSibling;
                        if (preview) {
                            preview.style.background = color;
                        }
                        // Refresh display
                        loadStats();
                    } else {
                        alert('Error updating color: ' + (data.error || 'Unknown error'));
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('Error updating color');
                });
        }
        
        function closeColorsModal() {
            document.getElementById('colors-modal').style.display = 'none';
        }
        
        function loadStats() {
            document.getElementById('loading').style.display = 'block';
            document.getElementById('stats-view').style.display = 'none';
            document.getElementById('settings-view').style.display = 'none';
            document.getElementById('calendar').innerHTML = '';
            document.getElementById('task-breakdown-content').innerHTML = '';
            
            Promise.all([
                fetch(`/api/stats?period=${currentPeriod}`).then(r => r.json()),
                fetch('/api/task-colors').then(r => r.json())
            ])
                .then(([statsData, colors]) => {
                    taskColors = colors;
                    displayStats(statsData);
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('loading').textContent = 'Error loading statistics';
                });
        }
        
        // Fetch stats on load
        loadStats();
        
        // Auto-refresh stats every 30 seconds to catch sessions completed via CLI
        let autoRefreshInterval = null;
        let lastSessionCount = 0;
        
        function startAutoRefresh() {
            if (autoRefreshInterval) return;
            autoRefreshInterval = setInterval(async () => {
                try {
                    const response = await fetch(`/api/stats?period=${currentPeriod}`);
                    const data = await response.json();
                    const currentSessionCount = data.sessions ? data.sessions.length : 0;
                    
                    // Only refresh the display if session count changed (new session added)
                    if (currentSessionCount !== lastSessionCount) {
                        lastSessionCount = currentSessionCount;
                        loadStats();
                        console.log('Auto-refreshed: New session detected');
                    }
                } catch (error) {
                    console.error('Auto-refresh check failed:', error);
                }
            }, 10000); // Check every 10 seconds
        }
        
        function stopAutoRefresh() {
            if (autoRefreshInterval) {
                clearInterval(autoRefreshInterval);
                autoRefreshInterval = null;
            }
        }
        
        // Start auto-refresh when page loads
        startAutoRefresh();
        
        // Pause auto-refresh when tab is hidden, resume when visible
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                stopAutoRefresh();
            } else {
                startAutoRefresh();
                // Immediately check for updates when tab becomes visible
                loadStats();
            }
        });
    </script>
</body>
</html>
'''

def format_time_display(seconds):
    """Format seconds into a human-readable string"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"

def display_terminal_stats(period='week'):
    """Display statistics in the terminal"""
    db = PomodoroDatabase()
    stats = db.get_stats(period=period)
    
    # Map period names for display
    period_names = {
        'week': 'Week',
        'month': 'Month',
        'year': 'Year',
        'all': 'All Time'
    }
    period_name = period_names.get(period, period.capitalize())
    
    print("=" * 70)
    print(f"🍅 POMODORO STATISTICS - {period_name.upper()}")
    print("=" * 70)
    print()
    
    # Total stats
    total_time = stats['total_time_all']
    total_sessions = stats['total_sessions']
    
    print(f"📊 Total Sessions: {total_sessions}")
    print(f"⏱️  Total Time: {format_time_display(total_time)}")
    print()
    
    # Time per task/event type
    tasks = stats['by_task']
    if tasks:
        print("=" * 70)
        print("📋 TIME BY TASK/EVENT TYPE")
        print("=" * 70)
        print()
        
        # Find max time for bar scaling
        max_time = max(task['total_time'] for task in tasks) if tasks else 1
        
        for task in tasks:
            task_name = task['task_name']
            task_time = task['total_time']
            task_count = task['count']
            percentage = (task_time / total_time * 100) if total_time > 0 else 0
            
            # Create a simple bar chart
            bar_length = 40
            filled = int((task_time / max_time) * bar_length) if max_time > 0 else 0
            bar = '█' * filled + '░' * (bar_length - filled)
            
            print(f"{task_name:<30} {format_time_display(task_time):>10} ({task_count:>3} sessions) {percentage:>5.1f}%")
            print(f"{'':30} {bar}")
            print()
    else:
        print("No sessions found for this period.")
        print("Start working to see your statistics!")
        print()
    
    # Daily breakdown (optional, show last 7 days for week, last 30 for month, etc.)
    by_date = stats['by_date']
    if by_date:
        print("=" * 70)
        print("📅 DAILY BREAKDOWN")
        print("=" * 70)
        print()
        
        # Show recent dates (limit to 10 most recent)
        for date_entry in by_date[:10]:
            date_str = date_entry['date']
            date_time = date_entry['total_time']
            date_count = date_entry['count']
            
            # Format date nicely
            try:
                date_obj = datetime.fromisoformat(date_str).date()
                formatted_date = date_obj.strftime('%Y-%m-%d (%a)')
            except:
                formatted_date = date_str
            
            print(f"{formatted_date:<25} {format_time_display(date_time):>10} ({date_count:>3} sessions)")
        
        if len(by_date) > 10:
            print(f"\n... and {len(by_date) - 10} more days")
        print()
    
    print("=" * 70)
    print("💡 Tip: Use 'pomodoro stats' to open the web interface for more details")
    print("=" * 70)

def main():
    parser = argparse.ArgumentParser(
        description='Pomodoro Timer - Track your work sessions',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  pomodoro learning_option_trading 1h
  pomodoro coding 30m
  pomodoro reading 2h
  pomodoro stats          (opens web interface)
  pomodoro s w            (terminal stats for week)
  pomodoro s m            (terminal stats for month)
  pomodoro s y            (terminal stats for year)
  pomodoro s a            (terminal stats for all time)
        '''
    )
    
    parser.add_argument('task', nargs='?', help='Task name (e.g., learning_option_trading) or "s" for stats')
    parser.add_argument('duration', nargs='?', help='Duration (e.g., 1h, 30m, 2h30m) or period (w/m/y/a) for stats')
    parser.add_argument('stats', nargs='?', help='Show stats (use: pomodoro stats)')
    
    args = parser.parse_args()
    
    # Check if user wants stats (web interface)
    if args.task == 'stats' or args.stats == 'stats':
        start_stats_server()
        return
    
    # Check if user wants terminal stats (pomodoro s w, pomodoro s m, etc.)
    if args.task == 's':
        period_map = {
            'w': 'week',
            'm': 'month',
            'y': 'year',
            'a': 'all'
        }
        period = period_map.get(args.duration, 'week')  # Default to week
        display_terminal_stats(period)
        return
    
    # Validate arguments
    if not args.task:
        parser.print_help()
        sys.exit(1)
    
    # Parse duration
    duration_seconds = parse_duration(args.duration) if args.duration else 25 * 60
    
    # Start timer
    timer = PomodoroTimer(task_name=args.task, duration_seconds=duration_seconds)
    timer.run()

if __name__ == "__main__":
    main()
