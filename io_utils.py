import serial
import sys
import warnings
import glob
import json

def detect_serial_port():
    """
    Detects the appropriate serial port based on the operating system.

    Returns:
        str: The detected serial port (e.g., "COM3" on Windows, "/dev/ttyUSB0" on Linux).
             Returns None if no suitable port is found.
    """
    if sys.platform.startswith('win'):
        # Check for available COM ports
        ports = [f'COM{i}' for i in range(1, 256)]  # Check COM1 to COM255
    elif sys.platform.startswith('linux'):
        # Check for available /dev/ttyUSB* devices
        ports = glob.glob('/dev/ttyUSB*')
    else:
        raise OSError("Unsupported operating system.")

    for port in ports:
        try:
            # Try opening the port to see if it's valid
            with serial.Serial(port) as ser:
                print(f"Found valid serial port: {port}")
                return port  # Return the first valid port found
        except serial.SerialException:
            pass  # Port is unavailable, continue to the next one

    return None  # No valid port found

def read_json_params(json_filepath):
    """Reads and returns parameters from a JSON file."""
    try:
        with open(json_filepath, 'r') as f:
            params = json.load(f)  # Now json.load() will work correctly
    except:
        raise Exception("Error reading or decoding JSON file.")
    return params

def read_experiment_file(filepath):
    """Reads and parses the experiment text file."""
    experiment_data = []
    with open(filepath, 'r') as f:
        next(f)  # Skip header line
        for line in f:
            if not line.strip():
                continue
            parts = line.strip().split('\t')
            experiment_data.append({
                "time": int(parts[0]),
                "pattern": parts[1],
                "valve_id": parts[2],
                # "1,1;2,1;3,2" -> [(1, 1), (2, 1), (3, 2)]
                "valves": [tuple(int(x) for x in bv.split(',')) for bv in parts[2].split(';')]
            })
    return experiment_data