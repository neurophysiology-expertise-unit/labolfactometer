import json
import numpy as np
from scipy.stats import expon, truncnorm
from datetime import datetime

def read_json_params(json_filepath):
    """Reads and returns parameters from a JSON file."""
    try:
        with open(json_filepath, 'r') as f:
            params = json.load(f)
    except:
        raise Exception("Error reading or decoding JSON file.")
    return params

def generate_events(params):
    """Generates the 'events' array based on the parameters."""
    odor_ID = np.array([np.binary_repr(i, width=params["odor_ID_bits"]) for i in range(1, params["n_valves"] + 1)])
    odor_OFF = np.binary_repr(0, width=params["odor_ID_bits"])

    events = []
    for i in range(params["n_valves"]):
        events.append(np.array([
            [
                params["punish_OFF"],
                params["reward_OFF"],
                params["opto_stim_OFF"],
                odor_OFF,
                params["thermal_cam_ON"]
            ],
            [
                params["punish_OFF"],
                params["reward_OFF"],
                params["opto_stim_OFF"],
                odor_ID[i],
                params["thermal_cam_OFF"]
            ],
            [
                params["punish_OFF"],
                params["reward_OFF"],
                params["opto_stim_OFF"],
                odor_OFF,  # Ensure odor is off
                params["thermal_cam_OFF"]
            ],
            [
                params["punish_OFF"],
                params["reward_OFF"],
                params["opto_stim_OFF"],
                odor_OFF,
                params["thermal_cam_OFF"]
            ]
        ]))
    return events

def generate_valve_commands(params):
    """Generates the 'valves' list based on the parameters (for multiple boards)."""
    # Calculate board and valve IDs for each odor
    board_valve_ids = []
    for i in range(params["n_valves"]):
        board_id = (i // 7) + 1
        valve_id = (i % 7) + 1
        board_valve_ids.append(f"{board_id},{valve_id}")

    # Construct the 'valves' list with correct board and valve IDs
    valves = []
    for i in range(params["n_valves"]):
        valve_entry = []
        for board_num in range(1, params["n_boards"] + 1):
            if board_num == (i // 7) + 1:
                valve_entry.append(board_valve_ids[i])
            else:
                valve_entry.append(f"{board_num},1")  # Default valve for other boards
        
        valves.append([";".join(valve_entry), "1,1"])

    return valves

def generate_iti(params):
    """Generates inter-trial intervals (ITIs) based on the parameters."""
    loc = 0
    scale = params["meanITI"]
    a, b = (params["ITI_min"] - loc) / scale, (params["ITI_max"] - loc) / scale
    truncated_expon = truncnorm(a, b, loc=loc, scale=scale)
    ITI = np.round(truncated_expon.rvs(size=params["n_trials"])).astype(int)
    return ITI

def create_session(params, intrvl, events, valves):
    """Creates the 'session' list based on the parameters and generated data."""
    print("Generating Familiar session...")
    tmp6 = np.random.permutation(params["n_trials"])
    tmp7 = np.tile(params["familiar_odor_sequence"], params["n_present"])
    ind1 = tmp7[tmp6]
    session = []
    for i in ind1:
        session.append([intrvl[i-1], events[i-1], valves[i-1]])
    return session

def assemble_experiment_file_data(params, session, ITI):
    """Assembles the data for the experiment file (col1, col2, col3)."""
    session_intervals = [item[0] for item in session]
    session_events = [item[1] for item in session]
    session_valves = [item[2] for item in session]

    # Flatten intervals and insert ITIs
    tmp3 = np.concatenate([interval.flatten() for interval in session_intervals])
    
    # Insert ITIs at the correct positions (after every 4 events)
    ITI_idx = np.arange(3, len(tmp3), 4) 
    tmp3 = np.insert(tmp3, ITI_idx, ITI)

    # Calculate cumulative sum of times for col1
    event_times = []
    current_time = 0
    for i, interval in enumerate(session_intervals):
        trial_times = np.cumsum(interval.flatten())
        trial_times += current_time  # Add the current time to the trial times
        current_time = trial_times[-1] + ITI[i % len(ITI)]  # Update current time for the next trial
        event_times.append(trial_times)

    # Flatten the updated event times for col1
    col1 = np.concatenate(event_times)

    # Prepare events for col2
    col2_flat = np.concatenate(session_events)
    col2_str = ["".join(event.astype(str)) for event in col2_flat]

    # Prepare valves for col3
    col3_str_all = []
    for i, valve_commands in enumerate(session_valves):
        # Repeat each valve command twice
        col3_str_all.extend([valve_commands[0]] * 1)  # First command once (open)
        col3_str_all.extend([valve_commands[1]] * 3)  # Second command three time (close)

    return col1, col2_str, col3_str_all

def save_text_file(col1, col2_str, col3_str, filename):
    """Saves the experiment data to a text file."""
    with open(f"{filename}.txt", "w") as f:
        f.write("Delta T [ms]\t16-bit pattern\tBoard ID,Valve ID;... \n")
        event_count = 0
        for i in range(len(col3_str)):
            f.write(f"{col1[event_count]}\t{col2_str[event_count]}\t{col3_str[i]}\n")
            event_count += 1

def save_mat_file(params, col1, col2_flat, col3_str, bits, filename):
    """Saves the experiment data to a .mat file (optional)."""
    if params.get("save_mat_file", False):
        from scipy.io import savemat

        times = {
            "log_start": [],
            "log_end": [],
            "trl_start": [],
            "trl_end": [],
            "odor": [],
            "punish": [],
            "reward": [],
            "opto": [],
            "odor_ON": [],
            "odor_OFF": [],
            "olfactomat_delay": []
        }

        col2_reshaped = col2_flat.reshape(-1, 5)
        trial = {
            "chemical": [],
            "odor_ID": np.unique(np.array([int(row[3], 2) for row in col2_reshaped if row[3] != '00000000'])),
            "ind": np.arange(1, params["n_trials"] + 1),
            "count": np.ones(params["n_trials"])
        }

        for i in range(1, params["n_valves"] + 1):
            indices = np.where(trial["odor_ID"] == i)[0]
            trial["count"][indices] = np.arange(1, len(indices) + 1)

        signal = {
            "breath": [],
            "pupil": [],
            "vid_index": []
        }
        n_day = []
        animal_ID = []

        savemat(f"{filename}.mat", {
            "col1": col1, "col2": col2_reshaped, "col3": col3_str, "params": params, "bits": bits,
            "times": times, "trial": trial, "signal": signal, "n_day": n_day, "animal_ID": animal_ID
        })

def generate_experiment_file(json_filepath):
    """
    Main function to generate the experiment .txt file.
    """
    params = read_json_params(json_filepath)

    # --- Experiment Parameters ---
    params["n_odors"] = params["n_familiar_odors"]
    params["n_present"] = params["n_presentations"]
    params["n_trials"] = params["n_odors"] * params["n_present"]

    # --- Fixed Parameters (can be moved to JSON if needed) ---
    params["n_preblock"] = 0  # No pre-block for familiar
    params["Novelty_task_char"] = "Familiar"
    params["odor_ID_bits"] = 8

    # --- Define bits ---
    bits = {
        "thermal_cam": params["bits"]["thermal_cam"],
        "odor_ID": np.array(params["bits"]["odor_ID"]),
        "opto_stim": params["bits"]["opto_stim"],
        "rewardID": np.array(params["bits"]["rewardID"]),
        "punishID": np.array(params["bits"]["punishID"])
    }

    # --- Generate Data ---
    intrvl = [np.array([0, params["ON"]["odor"] - params["trl_start"], params["OFF"]["odor"] - params["ON"]["odor"], params["trl_end"] - params["OFF"]["odor"]])] * params["n_valves"]
    events = generate_events(params)
    valves = generate_valve_commands(params)
    ITI = generate_iti(params)
    session = create_session(params, intrvl, events, valves)
    col1, col2_str, col3_str = assemble_experiment_file_data(params, session, ITI)

    # --- Save Files ---
    filename = f"Experiment_{datetime.now().strftime('%d%m%Y_%H%M%S')}"
    save_text_file(col1, col2_str, col3_str, filename)
    save_mat_file(params, col1, np.concatenate(events), col3_str, bits, filename)

    print(f"File '{filename}.txt' created.")

# --- Example Usage (if running this file directly) ---
if __name__ == "__main__":
    json_filepath = "experiment_generation_params.json"  # Use the correct JSON filename
    generate_experiment_file(json_filepath)