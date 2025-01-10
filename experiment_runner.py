import time
import serial
import asyncio
import csv
from datetime import datetime
import numpy as np
from scipy.stats import expon, truncnorm
import json
import sys
import warnings
import glob
import os

# Import functions from io_utils.py
from io_utils import detect_serial_port, read_json_params, read_experiment_file

# --- Constants ---
SOF = 0x0F
EOF = 0x04
DLE = 0x05
DRIVE = 0x44

# --- Serial Communication Functions ---
def command_string(board_id, valve, command=DRIVE):
    """Generates command bytes for a given valve on a given PCB board

    >>> [hex(x) for x in command_string("04049F01", 4, DRIVE)]
    ['0xf', '0x5', '0x4', '0x5', '0x4', '0x9f', '0x1', '0x44', '0x5', '0x4', '0x0', '0x0', '0x4']

    :param str board_id: Address of the board as a string of 4 bytes (e.g. '0A05F2DE')
    :param int valve: valve on the PCB board to send command to (1..8)
    :param int command: command to send to the channel: 0x44 = drive channel, 0x45: Enable relais driver, 0x49: Idle relais driver
    :returns bytearray of at least 10 bytes containing the command to be relayed to the solenodrive
    """
    assert command in (0x44, 0x45, 0x49), "Command byte not recognised, must be 0x44, 0x45 or 0x49"

    valve += 0x30
    board_array = [int(board_id[i:i+2], 16) for i in range(0, len(board_id), 2)]
    command_array = board_array + [command, valve]
    ind = 0
    while ind < len(command_array):
        # Add escape bytes (0x05) before any special characters,
        #   this way, they will be interpreted as data by the solenodrive
        if command_array[ind] in (SOF, EOF, DLE):
            command_array.insert(ind, 5)
            ind += 1
        ind += 1
    # Currently, the CRC checksum is ignored by the solenodrive firmware, so we just send 2 zeros instead
    #   With updates of the firmware, it might become necessary to add the correct CRC checksum
    return bytearray([SOF] + command_array + [0, 0] + [EOF])

class SerialOlfactometer(serial.Serial):
    """Solenodrive interface for triggering olfactometer
        This class can be used to interact with the solenodrive PCBs using the RS485 communication standard

    """

    BOARDS = ["0D02B017", "0D02B016", "0D02B015", "0D02B01E", "0D02C019"]

    def __init__(self, port="/dev/ttyUSB0", baudrate=57600, bytesize=serial.EIGHTBITS, timeout=None, **kwargs):
        """Create connection to the solenodrive"""
        if baudrate != 57600:
            warnings.warn("Baudrate used by solenodrive is 57600")
        super().__init__(port=port, baudrate=baudrate, bytesize=bytesize, timeout=timeout, **kwargs)

    def add_board(self, board_id):
        if board_id in self.BOARDS:
            return False
        self.BOARDS.append(board_id)
        return True

    def set_boards(self, board_ids):
        self.BOARDS = board_ids

    def get_boards(self):
        return self.BOARDS

    def remove_board(self, board_id):
        if board_id in self.BOARDS:
            self.BOARDS.remove(board_id)
            return True
        return False

    def open_valve(self, channel):
        """Open valve number 'channel'"""
        board_id, valve = divmod(channel-1, 8)
        cs = command_string(self.BOARDS[board_id], valve+1, command=DRIVE)
        return cs

    def close_valve(self, channel):
        """Close valve number 'channel'"""
        board_id, _ = divmod(channel-1, 8)
        return self.open_valve(board_id * 8 + 1)

    async def stimulate(self, channel, duration):
        """Open a channel for a specified duration (in seconds)"""
        self.open_valve(channel)
        await asyncio.sleep(duration)
        self.close_valve(channel)

# --- Experiment Runner Functions ---
def read_experiment_file(filepath):
    """Reads and parses the experiment text file."""
    experiment_data = []
    with open(filepath, 'r') as f:
        next(f)  # Skip header line
        for line in f:
            parts = line.strip().split('\t')
            experiment_data.append({
                "time": int(parts[0]),
                "pattern": parts[1],
                "valve_id": parts[2]
            })
    return experiment_data

async def run_experiment(experiment_file, olf_serial, log_file, params, bits):
    """
    Loads and runs the experiment based on the .txt file using the SerialOlfactometer object.
    Logs valve actions to the log_file and prints information to the console.
    """
    experiment_data = read_experiment_file(experiment_file)
    start_time = time.time() * 1000  # Convert to milliseconds

    # Keep track of the previous state of each odor bit for each valve
    valve_states = {}  # Dictionary to track valve states: {(board_id, valve_id): {bit_index: state}}

    with open(log_file, 'w', newline='') as csvfile:
        log_writer = csv.writer(csvfile)
        log_writer.writerow(["Timestamp", "Event Type", "Board ID", "Valve ID", "Bit Index", "Previous State", "New State"])

        for event in experiment_data:
            elapsed_time = time.time() * 1000 - start_time
            wait_time = (event["time"] - elapsed_time) / 1000  # Convert to seconds

            if wait_time > 0:
                await asyncio.sleep(wait_time)

            # Extract board and valve IDs
            board_id_str, valve_id_str = event["valve_id"].split(",")
            board_id = int(board_id_str)
            valve_id = int(valve_id_str)

            # Get the board ID from the list of boards
            board_id_hex = olf_serial.BOARDS[board_id - 1]

            # Send the command
            olf_serial.write(command_string(board_id_hex, valve_id))

            # Log the event and print to console
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')

            # Iterate through the odor bits and check for changes
            for bit_index in range(bits["odor_ID"][0], bits["odor_ID"][-1] + 1):
                bit_pos = 15 - bit_index
                new_state = event["pattern"][bit_pos]

                # Ensure the valve state for this board_id, valve_id, and bit_index is initialized
                if (board_id, valve_id) not in valve_states:
                    valve_states[(board_id, valve_id)] = {}

                # Get the previous state, defaulting to 'unknown' if not set
                prev_state = valve_states.get((board_id, valve_id), {}).get(bit_index, 'unknown')

                # Log the event if the state has changed
                if new_state != prev_state:
                    if new_state == '0':
                        event_type = "Odor Bit OFF"
                    else:
                        event_type = "Odor Bit ON"

                    log_writer.writerow([current_time, event_type, board_id, valve_id, bit_index, prev_state, new_state])
                    print(f"Time: {current_time} - Board {board_id}, Valve {valve_id}, Bit {bit_index}: {prev_state} -> {new_state}")

                    # Update the valve state
                    valve_states.setdefault((board_id, valve_id), {})[bit_index] = new_state
async def main():
    # Check if an experiment file is provided as a command-line argument
    if len(sys.argv) > 1:
        experiment_file = sys.argv[1]
        # Extract the directory of the experiment file
        experiment_file_dir = os.path.dirname(experiment_file)
        # Construct the path to the JSON file in the same directory
        json_filepath = os.path.join(experiment_file_dir, "input_params.json")
    else:
        # Ask the user for the JSON file path
        json_filepath = input("Please enter the path to the experiment JSON file: ")

    # Check if the JSON file exists
    if not os.path.exists(json_filepath):
        raise FileNotFoundError(f"JSON file not found: {json_filepath}")

    params = read_json_params(json_filepath)  # Use the imported function

    # --- Define bits ---
    bits = {
        "thermal_cam": params["bits"]["thermal_cam"],
        "odor_ID": np.array(params["bits"]["odor_ID"]),
        "opto_stim": params["bits"]["opto_stim"],
        "rewardID": np.array(params["bits"]["rewardID"]),
        "punishID": np.array(params["bits"]["punishID"])
    }

    log_file = f"Experiment_log_{datetime.now().strftime('%d%m%Y_%H%M%S')}.csv"

    # --- Detect the serial port using the function from io_utils.py ---
    serial_port = detect_serial_port()
    if serial_port is None:
        raise OSError("No suitable serial port found.")

    # Print the detected port
    print(f"Using serial port: {serial_port}")

    # Create an instance of SerialOlfactometer
    olf_serial = SerialOlfactometer(port=serial_port)
    olf_serial.set_boards(params["board_ids"])

    # Run the experiment
    await run_experiment(experiment_file, olf_serial, log_file, params, bits)

    olf_serial.close()

# --- Example Usage ---
if __name__ == "__main__":
    asyncio.run(main())