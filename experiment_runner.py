import time
import serial
import asyncio
import csv
from datetime import datetime
import json
import numpy as np

# --- Constants (make sure these match the values in experiment_generator.py) ---
SOF = 0x0F
EOF = 0x04
DLE = 0x05
DRIVE = 0x44

# --- Serial Communication Functions ---
def command_string(board_id, valve, command=DRIVE):
    # ... (same as in experiment_generator.py)
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

    BOARDS = ["0D02C01E", "0D02B016", "0D02B015", "0D02B01E", "0D02C019"]

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
    Logs valve actions to the log_file.
    """
    experiment_data = read_experiment_file(experiment_file)
    start_time = time.time() * 1000  # Convert to milliseconds

    with open(log_file, 'w', newline='') as csvfile:
        log_writer = csv.writer(csvfile)
        log_writer.writerow(["Timestamp", "Event Type", "Valve ID", "Pattern"])

        for event in experiment_data:
            elapsed_time = time.time() * 1000 - start_time
            wait_time = (event["time"] - elapsed_time) / 1000  # Convert to seconds

            if wait_time > 0:
                await asyncio.sleep(wait_time)

            # Extract board and valve IDs
            board_id_str, valve_id_str = event["valve_id"].split(",")
            board_id = int(board_id_str)
            valve_id = int(valve_id_str)

            olf_serial.write(command_string(olf_serial.BOARDS[board_id - 1], valve_id))

            # Log the event
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')

            # Determine if it's an open or close event based on the pattern
            if event["pattern"][15 - bits["odor_ID"][0]] == '1':
                event_type = "Valve Open"
            else:
                event_type = "Valve Close"

            log_writer.writerow([current_time, event_type, event["valve_id"], event["pattern"]])

async def main():
    json_filepath = "input_params.json"  # You might want to make this an argument
    params = read_json_params(json_filepath)

    # --- Define bits ---
    bits = {
        "thermal_cam": params["bits"]["thermal_cam"],
        "odor_ID": np.array(params["bits"]["odor_ID"]),
        "opto_stim": params["bits"]["opto_stim"],
        "rewardID": np.array(params["bits"]["rewardID"]),
        "punishID": np.array(params["bits"]["punishID"])
    }

    experiment_file = "Experiment_10012025_011248.txt"  # Replace with your experiment file
    log_file = f"Experiment_log_{datetime.now().strftime('%d%m%Y_%H%M%S')}.csv"

    # Create an instance of SerialOlfactometer
    olf_serial = SerialOlfactometer(port=params["serial_port"])
    olf_serial.set_boards([params["board_ids"]])

    # Run the experiment
    await run_experiment(experiment_file, olf_serial, log_file, params, bits)

    olf_serial.close()

# --- Helper function to read JSON parameters (you can also put this in a separate utils.py file)
def read_json_params(json_filepath):
    """Reads and returns parameters from a JSON file."""
    try:
        with open(json_filepath, 'r') as f:
            params = json.load(f)
    except:
        raise Exception("Error reading or decoding JSON file.")
    return params

# --- Example Usage ---
if __name__ == "__main__":
    asyncio.run(main())