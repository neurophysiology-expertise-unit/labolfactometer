function experiment_txt_generator_from_json(json_filepath)
% Generates an experiment .txt file for a 'Familiar' task with one board,
% based on parameters from a JSON file.

% --- Read JSON Data ---
try
    fid = fopen(json_filepath, 'r');
    raw = fread(fid, inf);
    str = char(raw');
    fclose(fid);
    params = jsondecode(str);
catch
    error('Error reading or decoding JSON file.');
end

% --- Main Experiment Generation Logic ---

% ---(a) Technicalities for NIdaq Board Control ---
bits.thermal_cam = params.bits.thermal_cam;
bits.odor_ID = params.bits.odor_ID;
bits.opto_stim = params.bits.opto_stim;
bits.rewardID = params.bits.rewardID;
bits.punishID = params.bits.punishID;

punish_ON = params.punish_ON;
punish_OFF = params.punish_OFF;
reward_ON = params.reward_ON;
reward_OFF = params.reward_OFF;
opto_stim_ON = params.opto_stim_ON;
opto_stim_OFF = params.opto_stim_OFF;
odor_ID = dec2bin(1:params.n_valves, params.odor_ID_bits);
odor_OFF = dec2bin(0, params.odor_ID_bits);
thermal_cam_ON = params.thermal_cam_ON;
thermal_cam_OFF = params.thermal_cam_OFF;

intrvl = repmat({[0; diff([params.trl_start; params.ON.odor; params.OFF.odor; params.trl_end])]}, 1, params.n_valves);

events = cell(1, params.n_valves);
for i = 1:params.n_valves
    events{i} = [ ...
        punish_OFF, reward_OFF, opto_stim_OFF, odor_OFF, thermal_cam_ON; ...
        punish_OFF, reward_OFF, opto_stim_OFF, odor_ID(i, :), thermal_cam_OFF; ...
        punish_OFF, reward_OFF, opto_stim_OFF, odor_OFF, thermal_cam_OFF; ...
        punish_OFF, reward_OFF, opto_stim_OFF, odor_OFF, thermal_cam_OFF];
end

% ---(b) Technicalities for Valve Control (Simplified for 1 board) ---
formatSpec = '%d,%d'; % Simplified for a single board
odor_valve = cell(1, params.n_valves);
for i = 1:params.n_valves
    boardID = 1; % Always board 1
    valveID = mod(i - 1, 7) + 1; % Assuming 7 valves per board
    odor_valve{i} = sprintf(formatSpec, boardID, valveID);
end

blank = '1,1';% Simplified for a single board
valves = cell(1,params.n_valves);
for i = 1:params.n_valves
    valves{i} = [blank; odor_valve{i}; blank; blank];
end

% ---(c) Parameters for Familiar Task ---
params.Novelty_task_char = 'Familiar';
params.n_odors = params.n_familiar_odors; 
params.n_preblock = 0; % No pre-block for familiar
params.n_present = params.n_presentations;
params.n_trials = params.n_odors * params.n_present;

% ---(d) Generate ITIs ---
pd = makedist('Exponential', 'mu', params.meanITI);
t = truncate(pd, params.ITI_min, params.ITI_max);
ITI = round(random(t, params.n_trials, 1));

% ---(e) Create Pseudorandom Trial Structure (Familiar) ---
fprintf('Generating Familiar session...\n');
tmp6 = randperm(params.n_trials);
tmp7 = repmat(params.familiar_odor_sequence, 1, params.n_present); 
ind1 = tmp7(tmp6);
session = [];
for i = ind1
    session = [session; [intrvl(i) events(i) valves(i)]];
end

% ---(f) Assemble Experiment File ---
tmp3 = cell2mat(session(:, 1));
tmp3(1:size(events{1}, 1):end) = ITI;
col1 = cumsum(tmp3);
col2 = cell2mat(session(:, 2));
col3 = cell2mat(session(:, 3)); % Always board 1, no need for conditional

% --- Save Experiment and Create Variables for Analysis ---
L = length(col1);
M = col1(L, 1) / (1000 * 60);
N = fix(M / 60);
O = M - N * 60;

% --- Save Text File ---
filename = ['Experiment', '_', datestr(datetime, 'ddmmYYYY_HHMMSS')];
fid = fopen([filename, '.txt'], 'w');
fprintf(fid, 'Delta T [ms]\t16-bit pattern\tBoard ID,Valve ID;... \n');
for i = 1:size(col1, 1)
    fprintf(fid, '%s\t%s\t%s\t\n', num2str(col1(i, :)), col2(i, :), col3(i, :));
end
fclose(fid);

% --- Save Mat File ---
times.log_start = [];
times.log_end = [];
times.trl_start = [];
times.trl_end = [];
times.odor = [];
times.punish = [];
times.reward = [];
times.opto = [];
times.odor_ON = [];
times.odor_OFF = [];
times.olfactomat_delay = [];
trial.chemical = [];
trial.odor_ID = nonzeros(bin2dec(col2(:, bits.odor_ID)));
trial.ind = (1:params.n_trials)'; % Simplified
trial.count = ones(params.n_trials, 1);

for i = 1:params.n_valves
    indices = find(trial.odor_ID == i);
    trial.count(indices) = transpose(1:numel(indices));
end

signal.breath = [];
signal.pupil = [];
trial.vid_index = [];
n_day = [];
animal_ID = [];

save(filename, ...
    'col1', 'col2', 'col3', 'params', 'bits', ...
    'times', 'trial', 'signal', 'n_day', 'animal_ID');

fprintf('File "%s" created.\n', filename);
end