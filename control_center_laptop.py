# control_center_laptop.py
import tkinter as tk
from tkinter import messagebox
import socket
import json
import threading
import time
import gspread
import statistics
from datetime import datetime
from PIL import Image, ImageTk
import cv2
from flask import Flask, render_template_string
import folium
import collections

# --- Configuration ---
SERVICE_ACCOUNT_FILE = 'credentials.json'
GOOGLE_SHEET_NAME = 'Pi transaction log'
LAPTOP_IP = '0.0.0.0'
SENSOR_AGENT_PORT = 65430
ACTUATOR_AGENT_PORT = 65431
FRAME_WIDTH, FRAME_HEIGHT = 640, 480

app = Flask(__name__)

class ControlCenterApp:
    def __init__(self, master):
        self.master = master
        self.master.title("Control Center Dashboard")

        # --- State Variables ---
        self.sensor_conn, self.actuator_conn = None, None
        self.sensor_addr, self.actuator_addr = None, None
        self.worksheet = None
        self.stop_threads = threading.Event()
        self.is_session_active = False
        self.last_frame = None
        self.clients_lock = threading.Lock()
        self.gps_data = {"sensor_pi": None, "actuator_pi": None}
        self.log_data = []
        self.latencies_ms = []
        
        # <<< MODIFICATION: Added thread-safe queue for actuator responses >>>
        self.response_queue = collections.deque()
        self.queue_lock = threading.Lock()

        # --- GUI Layout (Same as before) ---
        main_frame = tk.Frame(master)
        main_frame.pack(fill="both", expand=True)
        video_frame = tk.Frame(main_frame, bg="black")
        video_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        self.video_label = tk.Label(video_frame)
        self.video_label.pack()
        control_frame = tk.Frame(main_frame, width=300)
        control_frame.pack(side="right", fill="y", padx=10, pady=10)
        control_frame.pack_propagate(False)
        tk.Label(control_frame, text="System Status", font=("Helvetica", 16, "bold")).pack(pady=10)
        self.sensor_status_label = tk.Label(control_frame, text="Sensor Pi: Disconnected", fg="red", font=("Helvetica", 12))
        self.sensor_status_label.pack(pady=5, anchor='w')
        self.actuator_status_label = tk.Label(control_frame, text="Actuator Pi: Disconnected", fg="red", font=("Helvetica", 12))
        self.actuator_status_label.pack(pady=5, anchor='w')
        self.session_status_label = tk.Label(control_frame, text="Session: INACTIVE", fg="orange", font=("Helvetica", 12, "bold"))
        self.session_status_label.pack(pady=20)
        self.start_button = tk.Button(control_frame, text="Start Session", command=self.start_session, state=tk.DISABLED, bg="green", fg="white", font=("Helvetica", 12, "bold"))
        self.start_button.pack(pady=10, fill=tk.X)
        self.stop_button = tk.Button(control_frame, text="Stop Session & Save Log", command=self.stop_session, state=tk.DISABLED, bg="red", fg="white", font=("Helvetica", 12, "bold"))
        self.stop_button.pack(pady=5, fill=tk.X)

        # --- Backend Initialization ---
        self.setup_google_sheets()
        threading.Thread(target=self.capture_camera, daemon=True).start()
        threading.Thread(target=self.run_network_server, daemon=True).start()
        threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000), daemon=True).start()
        app.config['CONTROL_CENTER_INSTANCE'] = self
        self.update_video_feed()
        self.master.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_closing(self):
        if messagebox.askokcancel("Quit", "Do you want to quit? This will stop the session."):
            self.stop_session()
            self.stop_threads.set()
            self.master.destroy()

    def update_status_labels(self):
        # (Same as before)
        if self.sensor_conn: self.sensor_status_label.config(text="Sensor Pi: Connected", fg="green")
        else: self.sensor_status_label.config(text="Sensor Pi: Disconnected", fg="red")
        if self.actuator_conn: self.actuator_status_label.config(text="Actuator Pi: Connected", fg="green")
        else: self.actuator_status_label.config(text="Actuator Pi: Disconnected", fg="red")
        if self.sensor_conn and self.actuator_conn: self.start_button.config(state=tk.NORMAL)
        else: self.start_button.config(state=tk.DISABLED)

    def start_session(self):
        print("--- Session Started ---")
        self.is_session_active = True
        self.log_data = []
        self.latencies_ms = []
        with self.queue_lock: # Clear any old responses
            self.response_queue.clear()
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.session_status_label.config(text="Session: ACTIVE", fg="green")

    def stop_session(self):
        if not self.is_session_active: return
        print("--- Session Stopped ---")
        self.is_session_active = False
        self.generate_final_report()
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.session_status_label.config(text="Session: INACTIVE", fg="orange")

    # (capture_camera and update_video_feed are the same as before)
    def capture_camera(self):
        cap = cv2.VideoCapture(0)
        if not cap.isOpened(): print("❌ CRITICAL ERROR: Cannot open laptop camera."); return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        print("✅ Camera capture thread started.")
        while not self.stop_threads.is_set():
            ret, frame = cap.read()
            if ret:
                with self.clients_lock: self.last_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            time.sleep(1/30)
        cap.release()

    def update_video_feed(self):
        if self.last_frame is not None:
            with self.clients_lock: frame_copy = self.last_frame.copy()
            img = Image.fromarray(frame_copy); photo = ImageTk.PhotoImage(image=img)
            self.video_label.config(image=photo); self.video_label.image = photo
        self.master.after(33, self.update_video_feed)
    
    def run_network_server(self):
        # (Same as before)
        sensor_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM); sensor_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sensor_server.bind((LAPTOP_IP, SENSOR_AGENT_PORT)); sensor_server.listen()
        threading.Thread(target=self.accept_connections, args=(sensor_server, self.handle_sensor_pi), daemon=True).start()
        print(f"✅ Listening for Sensor Pi on port {SENSOR_AGENT_PORT}")
        actuator_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM); actuator_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        actuator_server.bind((LAPTOP_IP, ACTUATOR_AGENT_PORT)); actuator_server.listen()
        threading.Thread(target=self.accept_connections, args=(actuator_server, self.handle_actuator_pi), daemon=True).start()
        print(f"✅ Listening for Actuator Pi on port {ACTUATOR_AGENT_PORT}")

    def accept_connections(self, server_socket, handler_func):
        while not self.stop_threads.is_set():
            conn, addr = server_socket.accept()
            print(f"Accepted connection from {addr}")
            threading.Thread(target=handler_func, args=(conn, addr), daemon=True).start()

    # <<< MODIFICATION: handle_sensor_pi is now the main processing loop >>>
    def handle_sensor_pi(self, conn, addr):
        self.sensor_conn, self.sensor_addr = conn, addr
        self.master.after(0, self.update_status_labels)
        try:
            intro_data = conn.recv(1024); print(f"Sensor Pi identified: {json.loads(intro_data.decode('utf-8'))}")
            while not self.stop_threads.is_set():
                data = conn.recv(1024)
                if not data: break
                if self.is_session_active and self.actuator_conn:
                    sensor_packet = json.loads(data.decode('utf-8'))
                    # Forward the command to the actuator
                    command = {'voltage': sensor_packet['voltage'], 'status': sensor_packet['status']}
                    self.actuator_conn.sendall(json.dumps(command).encode('utf-8'))
                    
                    # --- Start of Detailed Logging Logic ---
                    t1 = sensor_packet['timestamp']
                    actuator_response = None
                    start_wait = time.time()
                    while time.time() - start_wait < 2.0: # 2 second timeout
                        with self.queue_lock:
                            if self.response_queue:
                                actuator_response = self.response_queue.popleft()
                                break
                        time.sleep(0.001)

                    if actuator_response:
                        t4 = time.time()
                        t2, t3 = actuator_response['t2'], actuator_response['t3']
                        dac_voltage_set = actuator_response.get('voltage_set', 'N/A')
                        
                        # Perform latency calculation
                        offset_sec = ((t2 - t1) + (t3 - t4)) / 2
                        corrected_t2_sec = t2 - offset_sec
                        true_latency_sec = corrected_t2_sec - t1
                        self.latencies_ms.append(true_latency_sec * 1000)

                        # Format data for logging
                        adc_voltage_str = f"{sensor_packet['voltage']:.4f}" if sensor_packet['status'] == "Proper" else "Junk Value"
                        dac_voltage_str = f"{dac_voltage_set:.4f}V" if isinstance(dac_voltage_set, float) else "N/A"
                        packet_num = len(self.log_data) + 1

                        new_log_row = [
                            packet_num,
                            datetime.fromtimestamp(t1).strftime('%H:%M:%S:%f'),
                            datetime.fromtimestamp(corrected_t2_sec).strftime('%H:%M:%S:%f'),
                            f"{true_latency_sec * 1000:.2f}",
                            adc_voltage_str,
                            sensor_packet['status'],
                            dac_voltage_str,
                            str(sensor_packet['gps'])
                        ]
                        self.log_data.append(new_log_row)
                        print(f"Logged Packet #{packet_num}, Delay: {true_latency_sec * 1000:.2f} ms")
                    else:
                        print("❌ Timed out waiting for actuator response.")
        except (ConnectionResetError, BrokenPipeError): print(f"ℹ Sensor Pi {addr} disconnected.")
        finally:
            self.sensor_conn.close(); self.sensor_conn = None
            self.master.after(0, self.update_status_labels)
            self.master.after(0, self.stop_session)

    # <<< MODIFICATION: handle_actuator_pi now just adds responses to the queue >>>
    def handle_actuator_pi(self, conn, addr):
        self.actuator_conn, self.actuator_addr = conn, addr
        self.master.after(0, self.update_status_labels)
        try:
            intro_data = conn.recv(1024); print(f"Actuator Pi identified: {json.loads(intro_data.decode('utf-8'))}")
            while not self.stop_threads.is_set():
                data = conn.recv(1024)
                if not data: break
                status_packet = json.loads(data.decode('utf-8'))
                self.gps_data["actuator_pi"] = status_packet.get('gps')
                with self.queue_lock:
                    self.response_queue.append(status_packet)
        except (ConnectionResetError, BrokenPipeError): print(f"ℹ Actuator Pi {addr} disconnected.")
        finally:
            self.actuator_conn.close(); self.actuator_conn = None
            self.master.after(0, self.update_status_labels)
            self.master.after(0, self.stop_session)
    
    def setup_google_sheets(self):
        # (Same as before)
        try:
            gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE); spreadsheet = gc.open(GOOGLE_SHEET_NAME)
            today_sheet_name = datetime.now().strftime('%Y-%m-%d')
            try: self.worksheet = spreadsheet.worksheet(today_sheet_name)
            except gspread.exceptions.WorksheetNotFound: self.worksheet = spreadsheet.add_worksheet(title=today_sheet_name, rows="1000", cols="20")
            print(f"✅ Successfully connected to Google Spreadsheet: '{GOOGLE_SHEET_NAME}'")
        except Exception as e: print(f"❌ Google Sheets setup failed: {e}."); self.worksheet = None
            
    # <<< MODIFICATION: generate_final_report now uses the new detailed format >>>
    def generate_final_report(self):
        if not self.worksheet or not self.log_data:
            print("\nNo data to log or worksheet unavailable, skipping report."); return
        print("\n--- Generating Final Report ---")
        
        avg_latency = statistics.mean(self.latencies_ms) if self.latencies_ms else 0
        
        separator_block = [[], ["--- New Test Run ---", f"Timestamp: {datetime.now().strftime('%H:%M:%S')}"], []]
        
        summary_data = [
            ["Connection Time", datetime.now().strftime('%H:%M:%S:%f')],
            ["Connected To (Pi2)", f"{self.actuator_addr[0]}:{self.actuator_addr[1]}" if self.actuator_addr else "N/A"],
            ["Average Commu Delay", f"{avg_latency:.2f} ms"]
        ]
        
        header = ["Packet #", "Pi1 Send Time", "Corrected Pi2 Receive Time", "Delay (ms)", "ADC Sensor Voltage", "Data Status", "DAC Voltage Set (V)", "Sensor Pi GPS"]
        
        full_report_block = separator_block + summary_data + [[]] + [header] + self.log_data
        
        try:
            self.worksheet.append_rows(full_report_block, value_input_option='USER_ENTERED')
            print(f"✅ Report successfully APPENDED to sheet '{self.worksheet.title}'!")
        except Exception as e:
            print(f"❌ ERROR: Failed to write to Google Sheet. Error: {e}")

# (Flask @app.route and main function are the same as before)
@app.route('/')
def index():
    control_center = app.config['CONTROL_CENTER_INSTANCE']; gps_data = control_center.gps_data
    map_center, zoom_level = [20, 0], 2
    if gps_data['sensor_pi'] and gps_data['actuator_pi']:
        lat1, lon1 = gps_data['sensor_pi']; lat2, lon2 = gps_data['actuator_pi']
        map_center = [(lat1 + lat2) / 2, (lon1 + lon2) / 2]; zoom_level = 16
    elif gps_data['sensor_pi']: map_center = gps_data['sensor_pi']; zoom_level = 16
    elif gps_data['actuator_pi']: map_center = gps_data['actuator_pi']; zoom_level = 16
    m = folium.Map(location=map_center, zoom_start=zoom_level, tiles="OpenStreetMap")
    if gps_data['sensor_pi']: folium.Marker(location=gps_data['sensor_pi'], popup="Sensor Pi", tooltip="Sensor Pi", icon=folium.Icon(color='red', icon='car', prefix='fa')).add_to(m)
    if gps_data['actuator_pi']: folium.Marker(location=gps_data['actuator_pi'], popup="Actuator Pi", tooltip="Actuator Pi", icon=folium.Icon(color='blue', icon='truck', prefix='fa')).add_to(m)
    if gps_data['sensor_pi'] and gps_data['actuator_pi']: folium.PolyLine(locations=[gps_data['sensor_pi'], gps_data['actuator_pi']], color='green', weight=5, opacity=0.8).add_to(m)
    return m._repr_html_()

def main():
    root = tk.Tk()
    # Correcting the bug from before, ensuring __init__ is called correctly
    app_gui = ControlCenterApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
