"""NI-DAQ side of the old LabVIEW VI: continuous AI -> .tdms, and the two static DO ports.

Needs `nidaqmx` (and NI-DAQmx drivers) on the rig PC; imported lazily so the GUI and the
runner still work without it (dry run / serial only).
The .tdms layout matches the LabVIEW files: group "Measured Data", channels "AI0".."AI15",
waveform properties wf_start_time / wf_increment / NI_ChannelName / unit_string, plus a .tdms_index file
(segment headers only; readers use it to open the file faster and work without it).
"""
import time
from datetime import datetime, timezone

import numpy as np
from nptdms import ChannelObject, TdmsWriter


def bits(value, n):
    """int -> [bit0, bit1, ...] as bools; line k of a DO task gets bit k."""
    return [bool(value >> k & 1) for k in range(n)]


class DigitalOut:
    """16-bit event pattern + 7-bit event counter on static DO lines.

    The pattern string goes out in reading order, as LabVIEW wrote it: character k to the k-th line of
    `pattern_lines` (first character -> port1/line0). On the rig the facecam line follows character 1,
    high until the trial's last row. The counter is a number: bit k to line k of `counter_lines`.
    """

    def __init__(self, pattern_lines, counter_lines):
        import nidaqmx
        from nidaqmx.constants import LineGrouping
        self.tasks = []
        for lines in (pattern_lines, counter_lines):
            task = nidaqmx.Task()
            task.do_channels.add_do_chan(lines, line_grouping=LineGrouping.CHAN_PER_LINE)
            self.tasks.append(task)

    def write(self, pattern, counter):
        pat, cnt = self.tasks
        if len(pattern) != pat.number_of_channels:
            raise ValueError(f"pattern {pattern!r} has {len(pattern)} characters for {pat.number_of_channels} lines")
        pat.write([c == "1" for c in pattern])
        cnt.write(bits(counter, cnt.number_of_channels))

    def close(self):
        for task in self.tasks:
            task.close()


class Acquisition:
    """Continuous analog input written to a .tdms file in `chunk`-sample segments (LabVIEW used 300)."""

    def __init__(self, channels, fs, path, terminal="RSE", chunk=300, keep=60):
        import nidaqmx
        from nidaqmx.constants import AcquisitionType, TerminalConfiguration
        self.path, self.chunk, self.fs = path, chunk, fs
        self.task = nidaqmx.Task()
        self.task.ai_channels.add_ai_voltage_chan(channels, terminal_config=TerminalConfiguration[terminal])
        self.task.timing.cfg_samp_clk_timing(fs, sample_mode=AcquisitionType.CONTINUOUS, samps_per_chan=fs * 10)
        self.names = [c.physical_channel.name for c in self.task.ai_channels]  # "Dev1/ai0", ...
        self.task.register_every_n_samples_acquired_into_buffer_event(chunk, self._on_chunk)
        self.writer = TdmsWriter(str(path), index_file=True)  # also <path>_index, as LabVIEW writes
        self.n_samples = 0
        self.last = np.zeros(len(self.names))  # latest value per channel, for the GUI
        self.recent = np.zeros((len(self.names), int(fs * keep)))  # ring buffer of the last `keep` s, for the GUI
        self.error = None

    def start(self):
        self.writer.open()
        # UTC with microseconds, like LabVIEW (np.datetime64("now") truncates to seconds)
        self.t_start = np.datetime64(datetime.now(timezone.utc).replace(tzinfo=None), "us")
        self.task.start()

    def _on_chunk(self, task_handle, event_type, n, callback_data):
        try:
            data = np.atleast_2d(np.asarray(self.task.read(number_of_samples_per_channel=n), dtype=float))
            first = self.n_samples == 0
            self.writer.write_segment([
                ChannelObject("Measured Data", "AI" + name.split("ai")[-1], data[k], properties={
                    "wf_start_time": self.t_start, "wf_start_offset": 0.0, "wf_increment": 1 / self.fs,
                    "NI_ChannelName": name, "unit_string": "Volts"} if first else {})
                for k, name in enumerate(self.names)])
            self.remember(data)
        except Exception as e:  # surfaced in the GUI; acquisition stops instead of dying silently
            self.error = e
            return 1
        return 0

    def remember(self, data):
        """Append a (channels, n) chunk to the ring buffer and the sample count."""
        size, n = self.recent.shape[1], data.shape[1]
        kept = data[:, -size:]
        self.recent[:, (self.n_samples + n - kept.shape[1] + np.arange(kept.shape[1])) % size] = kept
        self.n_samples += n
        self.t_chunk = time.perf_counter()  # when the newest sample arrived, to place the traces in time
        self.last = data[:, -1]

    def recent_data(self, seconds):
        """The last `seconds` (at most `keep`) of every channel, oldest first."""
        size = self.recent.shape[1]
        n = min(self.n_samples, int(seconds * self.fs), size)
        return self.recent[:, (self.n_samples - n + np.arange(n)) % size]

    def stop(self):
        self.task.stop()
        self.task.close()
        self.writer.close()
