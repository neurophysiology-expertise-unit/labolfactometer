import serial
import warnings
import asyncio

SOF = 0x0F
EOF = 0x04
DLE = 0x05
DRIVE = 0x44

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


class OlfSerial(serial.Serial):
    """Solenodrive interface for triggering olfactometer
        This class can be used to interact with the solenodrive PCBs using the RS485 communication standard

    """

    BOARDS = ["0D02C01E", "0D02B016", "0D02B015", "0D02B01E", "0D02C019"]

    def __init__(self, port="COM3", baudrate=57600, bytesize=serial.EIGHTBITS, timeout=None, **kwargs):
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


if __name__ == '__main__':
    print(command_string("0D02B017", 5))
    ser = OlfSerial()
    for i in range(5):
        for j in range(2,9):
            ser.stimulate(8*i+j, 0.5)
    ser.close()
