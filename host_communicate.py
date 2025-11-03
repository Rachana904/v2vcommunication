# Host.py (Revised to implement Task 1, 2, and 3)
import socket
import json
import time
import statistics
import gspread
from datetime import datetime
import busio
import digitalio
import board
from adafruit_mcp3xxx.mcp3008 import MCP3008
from adafruit_mcp3xxx.analog_in import AnalogIn

# --- 1. CONFIGURATION CONSTANTS ---
SERVICE_ACCOUNT_FILE = 'credentials.json'
GOOGLE_SHEET_NAME = 'Pi transaction log'
HOST, PORT = '', 65434

# --- 2. SETUP FUNCTIONS (No changes needed here) ---
def setup_adc():
    """Initializes the MCP3008 using the Adafruit CircuitPython library."""
    try:
        spi = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)
        cs = digitalio.DigitalInOut(board.D5) # Using GPIO5 for CS
        mcp = MCP3008(spi, cs)
        chan0 = AnalogIn(mcp, 0)
        print("✅ Adafruit MCP3008 ADC interface initialized successfully.")
        return chan0
    except Exception as e:
        print(f"❌ ERROR: Could not initialize ADC. Real readings will fail. Error: {e}")
        return None

def setup_google_sheets():
    """Connects to Google Sheets and gets or creates today's worksheet."""
    try:
        gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        print(f"✅ Successfully connected to Google Spreadsheet: '{GOOGLE_SHEET_NAME}'")
        today_sheet_name = datetime.now().strftime('%Y-%m-%d')
        try:
            worksheet = spreadsheet.worksheet(today_sheet_name)
            print(f"✅ Found existing sheet for today: '{today_sheet_name}'")
        except gspread.exceptions.WorksheetNotFound:
            print(f"ℹ Sheet for '{today_sheet_name}' not found. Creating a new one.")
            worksheet = spreadsheet.add_worksheet(title=today_sheet_name, rows="1000", cols="20")
            worksheet.update('A1', [["--- This sheet contains all test runs from this date ---"]], value_input_option='USER_ENTERED')
        return worksheet
    except Exception as e:
        print(f"❌ ERROR: Could not connect to Google Sheets. Error: {e}")
        return None

# --- 3. CORE LOGIC FUNCTIONS ---
def get_sensor_reading(adc_channel):
    """Reads voltage from the provided ADC channel and determines its status."""
    if not adc_channel:
        return 0.0, "Junk (No ADC)"
    try:
        voltage = adc_channel.voltage
        status = "Proper" if voltage > 0.1 else "Junk (Disconnected)"
        return voltage, status
    except Exception as e:
        print(f"Read Error: {e}")
        return 0.0, "Junk (Read Error)"

def run_communication_loop(conn, adc_channel):
    """Continuously sends sensor data and records latency."""
    all_log_data, latencies_ms = [], []
    
    # --- TASK 2 CHANGE ---
    # Flag to track if we've seen a real voltage source yet.
    voltage_source_detected = False
    
    print("\n--- Waiting for voltage source on ADC... (Press Ctrl+C to stop) ---")
    try:
        packet_counter = 0
        while True:
            packet_counter += 1
            sensor_voltage, sensor_status = get_sensor_reading(adc_channel)

            # --- TASK 2 CHANGE ---
            # Check if a proper voltage source has just been connected.
            if sensor_status == "Proper" and not voltage_source_detected:
                print("\n✅ Voltage source detected! Starting real data transmission.\n")
                voltage_source_detected = True

            packet_to_send = {'voltage': sensor_voltage, 'status': sensor_status}
            t1 = time.time()
            conn.sendall(json.dumps(packet_to_send).encode('utf-8'))
            print(f"-> Packet {packet_counter} sent. Status: {sensor_status}, Voltage: {sensor_voltage:.2f}V")
            
            data_report = conn.recv(1024)
            if not data_report:
                print("Guest closed connection.")
                break
            
            t4 = time.time()
            pi2_data = json.loads(data_report.decode('utf-8'))
            t2, t3 = pi2_data['t2'], pi2_data['t3']
            
            # --- TASK 3 CHANGE ---
            # Get the reported DAC voltage from the guest's response packet.
            dac_voltage_set = pi2_data.get('dac_voltage_set', 'N/A')

            offset_sec = ((t2 - t1) + (t3 - t4)) / 2
            corrected_t2_sec = t2 - offset_sec
            true_latency_sec = corrected_t2_sec - t1
            latencies_ms.append(true_latency_sec * 1000)
            
            # --- TASK 1 CHANGE ---
            # If the status is not "Proper", log "Junk Value" instead of the voltage number.
            logged_sensor_voltage = f"{sensor_voltage:.4f}V" if sensor_status == "Proper" else "Junk Value"
            logged_dac_voltage = f"{dac_voltage_set:.4f}V" if isinstance(dac_voltage_set, float) else "N/A"

            all_log_data.append([
                packet_counter,
                datetime.fromtimestamp(t1).strftime('%H:%M:%S:%f'),
                datetime.fromtimestamp(corrected_t2_sec).strftime('%H:%M:%S:%f'),
                f"{true_latency_sec * 1000:.2f}",
                logged_sensor_voltage,
                sensor_status,
                # --- TASK 3 CHANGE ---
                logged_dac_voltage
            ])
            time.sleep(0.5)
            
    except KeyboardInterrupt:
        print("\nStopping transmission loop.")
    except (ConnectionResetError, BrokenPipeError):
        print("\nGuest connection lost.")
        
    return all_log_data, latencies_ms

def generate_final_report(worksheet, log_data, latencies_ms, connection_details):
    """Appends the summary and detailed logs of the test run to the worksheet."""
    if not log_data:
        print("\nNo data was transmitted, skipping report generation.")
        return
        
    print("\n--- Generating Final Report ---")
    avg_latency = statistics.mean(latencies_ms) if latencies_ms else 0
    
    separator_block = [[], ["--- New Test Run ---", f"Timestamp: {datetime.now().strftime('%H:%M:%S')}"], []]
    summary_data = [
        ["Connection Time", connection_details["time"]],
        ["Connected To (Pi2)", connection_details["addr"]],
        ["Average Communication Delay", f"{avg_latency:.2f} ms"]
    ]
    
    # --- TASK 3 CHANGE ---
    # Add the new column header for the DAC voltage.
    header = ["Packet #", "Pi1 Send Time", "Corrected Pi2 Receive Time", "Delay (ms)", 
              "ADC Sensor Voltage", "Data Status", "DAC Voltage Set (V)"]
              
    detailed_sheet_data = [header] + log_data
    
    full_report_block = separator_block + summary_data + [[]] + detailed_sheet_data
    
    try:
        worksheet.append_rows(full_report_block, value_input_option='USER_ENTERED')
        print(f"✅ Report successfully APPENDED to sheet '{worksheet.title}'!")
    except Exception as e:
        print(f"❌ ERROR: Failed to write to Google Sheet. Error: {e}")

# --- 4. MAIN EXECUTION BLOCK (No changes needed here) ---
def main():
    """The main function to orchestrate the entire process."""
    adc_channel = setup_adc()
    worksheet = setup_google_sheets()
    if not worksheet:
        print("Aborting due to Google Sheets connection failure."); return
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((HOST, PORT))
        server_socket.listen(1)
        print("\nHost is live. Waiting for a guest to connect...")
        try:
            conn, client_address = server_socket.accept()
            with conn:
                connection_details = {
                    "time": datetime.now().strftime('%H:%M:%S:%f'),
                    "addr": f"{client_address[0]}:{client_address[1]}"
                }
                print(f"✅ Guest connected from {client_address}")
                collected_data, collected_latencies = run_communication_loop(conn, adc_channel)
                if collected_data:
                    generate_final_report(worksheet, collected_data, collected_latencies, connection_details)
        except KeyboardInterrupt:
            print("\nServer stopped by user before connection.")

if __name__ == "__main__":
    main()
