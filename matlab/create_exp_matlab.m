function create_exp_matlab(Novelty_task, meanITI)
%Function generates an experiment .txt file which can be loaded into LV to run a novelty task.

%First define some defaults================================================
n_boards = 6; %n_boards=ceil(n_odors/7);  %1 board = 7 odorants
board_ID=1:n_boards;

if nargin<1
    Novelty_task = 0;
end

if nargin<2
    meanITI = 15000; %mean of the distribution for the random number generation (in ms) % meanITI for Left-Right of José is 20s
end

% n_trials=n_odors*n_present; %number of trials, without pre-block
% n_trials = n_odors*n_present; % 6 odors * 10 presentations
n_valves=35;
trl_start=0;
trl_end=12000; %in ms
ON.odor=4000;
OFF.odor=6000;
ON.reward=[];
OFF.reward=[];
ON.punish=[];
ON.punish=[];


%Second: some technicalities to power the NIdaq board:=====================
%define 16bit event ID patterns. Keep in mind that bit
%counts are from right to left in Matlab (dec2bin)
digit=dec2bin(0,16); %indicates which bits control which part of the NI data acquisition board
bits.thermal_cam=16; %indicates which bits control which part of the NI data acquisition board
bits.odor_ID=(8:15); %indicates which bits control which part of the NI data acquisition board
bits.opto_stim=7;    %indicates which bits control which part of the NI data acquisition board
bits.rewardID=(4:6); %indicates which bits control which part of the NI data acquisition board
bits.punishID=(1:3); %indicates which bits control which part of the NI data acquisition board
%bit(12) determines the state of the punishment valve
punish_ON='001';
punish_OFF=dec2bin(0,3);
%bit(11) determines the state of the reward port
reward_ON='001';
reward_OFF=dec2bin(0,3);
%bit(10) determines the state of the optogenetic illumination
opto_stim_ON='1'; 
opto_stim_OFF='0';
%bit(9:2) define the odorant ID in decimal
odor_ID=dec2bin(1:n_valves,8); %the 8 at the end indicates how many bits are used. In this case 8 bits.
odor_OFF=dec2bin(0,8);        %again 8 bits in length
%bit(1) determines the state of the thermal cam
thermal_cam_ON='1';
thermal_cam_OFF='0';

%generate times of events in the trial: each trial is given 4 events:
intrvl=repmat({[0; diff([trl_start; ON.odor; OFF.odor; trl_end])]},1,n_valves); %repmat (matrix A, vertical repetitions, horizontal repetitions)
%intrvl gives the possibility to present different odors for different amounts of time

%generate 16bit event ID pattern for all possible trial types. 
%every trial conssits out of 4 events.
events=[];
for i=1:n_valves
    events{i}= [ ...
        punish_OFF,reward_OFF,opto_stim_OFF,odor_OFF,thermal_cam_ON; ...
        punish_OFF,reward_OFF,opto_stim_OFF,odor_ID(i,:),thermal_cam_OFF; ...
        punish_OFF,reward_OFF,opto_stim_OFF,odor_OFF,thermal_cam_OFF; ...
        punish_OFF,reward_OFF,opto_stim_OFF,odor_OFF,thermal_cam_OFF]; %How does the camera know how long to record? There is no off-bit signal sent to the camera?
end

%Third: some technicalities to open the valves of the solecnoid drives:====
tmp1=ones(1,n_boards*2)';    %creates the 1,1 vector that is needed for each board [1,1;] = {board,valve;}
tmp1(1:2:end)=1:n_boards;    %adjusts the vector using 2 as increment, gives the second board the number 2. because 1:n_boards creates a vector of 1, 2, 3... until n is reached.
%tmp3=[1,1]'; --> Martijn's note: redundant? %creates vector that starts with 1 and ends with 2. (transposed to vertical)
tmp2=repmat(tmp1,1,n_valves); % repeats the matrix for the amount of odors. e.g. 2x7 for 7 odors. just copies the [1,2] colums, the second, fourth, (n*2)th... row will always indicate the valve ID, the first, third, (n*2-1)th row the board ID.

formatSpec=(['%d,%d',repmat(';%d,%d',1,n_boards-1)]); %creates a array that contains '%d, %d' for every board, % is a conversion specifier.
blank=sprintf(formatSpec, tmp1); %sprintf = print string after format
for i=1:n_valves
    boardID=ceil(bin2dec(odor_ID(i,:))/7); %binary variables are stored as a matrix, and each complete row  is one binary number (hence the (i,:)).
    valveID=1+bin2dec(odor_ID(i,:))-(ceil(bin2dec(odor_ID(i,:))/7)-1)*7; %This line of code serves the purpose of changing valves from base 8 to base 7, because every 8 valves, only 7 valves can code odorants, since 1 valve is blank.
    tmp2(boardID*2,i)=valveID;
    odor_valve{i}=sprintf(formatSpec, tmp2(:,i));
end

for i=1:n_valves        %makes sure the number of valve states matches the number of events in each trial.
    valves{i}= [blank; ...
        odor_valve{i}; ...
        blank; ...
        blank];
end

%Fourth: determine exact experiment:=======================================
switch Novelty_task
    case 2
        Novelty_task_char = 'Muscimol';
        %familiar trials block
        n_odors=12;
        n_preblock = 10;
        pre_block=n_preblock;  %n_familiar * number of desired trials 4*5
        n_present=7;
        n_trials = n_odors*n_present;
    case 1
        Novelty_task_char = 'Novel';
        %familiar trials block
        n_odors=14;
        n_preblock = 20;
        pre_block=n_preblock;  %n_familiar * number of desired trials 4*5
        n_present=10;
        n_trials = n_odors*n_present;
    case 0
        Novelty_task_char = 'Familiar';
        %familiar trials block
        n_odors=6;
        pre_block=0;  %n_familiar * number of desired trials 4*5
        n_present=10;
        n_trials = n_odors*n_present;
    otherwise 
        Novelty_task_char = [];
        pre_block=0;
        n_odors=6;
        pre_block=0;  %n_familiar * number of desired trials 4*5
        n_present=10;
        n_trials = n_odors*n_present;
end

fprintf('There are: \n - %d odors \n - %d board(s) \n Each trial lasts %2d seconds \n Every odorant condition is presented %d times \nThe meanITI is %2d.\n',n_valves,n_boards,trl_end/1000,n_present,meanITI);

%generate ITI's
pd = makedist('Exponential','mu',meanITI); %generate probability distribution object
t = truncate(pd,10000,20000); %truncate the distribution to the max and min desired --> 5 to 20 seconds.
ITI = round(random(t,n_trials+pre_block,1)); %draw intervals from exponential distribution with mean A

%create pseudorandom trial structure
session=[];

if Novelty_task == 1 %Day 5 novelty
    fprintf('Novelty session will be generated: /n');
    tmp4=randperm(pre_block);  %draw n_trials random numbers without replacing
    tmp5=repmat([1 2 3 9 10],1,4);  %create vector that has 1:n_odors, repeated n_present times
    ind=tmp5(tmp4);  %create index list
    for i=ind
        session=[session; [intrvl(i) events(i) valves(i)]];
    end
    
    tmp6 = randperm(n_odors*n_present);
    tmp7 = repmat([2:7 9:14],1,n_present);
    ind1=tmp7(tmp6);
    for i = ind1
        session=[session; [intrvl(i) events(i) valves(i)]];
    end
elseif Novelty_task == 2 %Day 6 MUSCIMOL
    fprintf('Muscimol session will be generated: /n');
    tmp4=randperm(pre_block);  %draw n_trials random numbers without replacing
    tmp5=repmat([1 2 3 9 10],1,2);  %create vector that has 1:n_odors, repeated n_present times
    ind=tmp5(tmp4);  %create index list
    for i=ind
        session=[session; [intrvl(i) events(i) valves(i)]];
    end
    
    tmp6 = randperm(n_odors*n_present);
    tmp7 = repmat([2:7 9:14],1,n_present);
    ind1=tmp7(tmp6);
    for i = ind1
        session=[session; [intrvl(i) events(i) valves(i)]];
    end
else %Familiar
    fprintf('Familiar session will be generated: /n');
    tmp6 = randperm(n_odors*n_present);
    tmp7 = repmat([1:3 8:10],1,n_present); %the odorants coded here are written in 'base 7' e.g. [22:28] are the odorants of board 3
    ind1=tmp7(tmp6);
    for i = ind1
        session=[session; [intrvl(i) events(i) valves(i)]];
    end
end

% Assemble experiment file

%ind=randsample(1:size(events,2),n_trials,true,weights);
% tmp4=randperm(n_odors*n_present);  %draw n_trials random numbers without replacing
% tmp5=repmat(1:n_odors,1,n_present);  %create vector that has 1:n_odors, repeated n_present times
% ind=tmp5(tmp4);  %create index list
% for i=ind
%     session=[session; [intrvl(i) events(i) valves(i)]];
% end

%different parts so that we can hard-code different parts of the code.
%part 1: F1+F2+F3+F4+F5+F6
% tmp6 = randperm(n_odors*n_present);
% % tmp7 = repmat([1:3 8:10],1,n_present); %the odorants coded here are written in 'base 7' e.g. [22:28] are the odorants of board 3
% tmp7 = repmat([2:7 9:14],1,n_present);
% ind1=tmp7(tmp6);
% for i = ind1
%     session=[session; [intrvl(i) events(i) valves(i)]];
% end
% %part 2: F1+F2+F3(2)+F4(2)+F5(2)+F6(2)
% tmp8 = randperm(6*n_present);
% tmp9 = repmat([1:2 10:13],1,n_present);
% ind2=tmp9(tmp8);
% for i = ind2;
%     session=[session; [intrvl(i) events(i) valves(i)]];
% end
% 
% %part 3: F1+F2+F3+F4+F5+F6
% tmp10 = randperm(6*n_present);
% tmp11 = repmat([1:6],1,n_present);
% ind3=tmp11(tmp10);
% for i = ind3;
%     session=[session; [intrvl(i) events(i) valves(i)]];
% end
% 
% %part 4: F1+F2+N1+N2+N3
% tmp12 = randperm(5*n_present);
% tmp13 = repmat([1:2 7:9],1,n_present);
% ind4=tmp13(tmp12);
% for i = ind4;
%     session=[session; [intrvl(i) events(i) valves(i)]];
% end
% 
% %part 5: F1+F2+N1+N2(2)+N3(2)
% tmp14 = randperm(5*n_present);
% tmp15 = repmat([1:2 7 14:15],1,n_present);
% ind5=tmp15(tmp14);
% for i = ind5;
%     session=[session; [intrvl(i) events(i) valves(i)]];
% end
% 
% %part 6: F1+F2+N1+N2+N3
% tmp16 = randperm(5*n_present);
% tmp17 = repmat([1:2 7:9],1,n_present);
% ind6=tmp17(tmp16);
% for i = ind6;
%     session=[session; [intrvl(i) events(i) valves(i)]];
% end


%assemble experiment file content
tmp3=cell2mat(session(:,1));
tmp3(1:size(events{1},1):end)=ITI;  %add ITI
col1=cumsum(tmp3);              %these are the event times
col2=cell2mat(session(:,2));    %this are the 16bit event patterns
if n_valves>8                    %the T&M LabView script has a bug; it cannot run with less than 2 valve drivers.
    col3=cell2mat(session(:,3));    %these are the valve commands
else
    col3=[cell2mat(session(:,3)),repmat(';2,1',[size(col1,1),1])];    %ADD 2,1 after the command for the first valve driver
end

% save experiment + create variables for analysis

L = length(col1);
M = [col1(L,1)/(1000*60)];
N = fix(M/60);
O = M-N*60;
fprintf('The file contains %d trials, %d events and lasts %g h and %2.0f min (%g minutes))\n',n_trials,L,N,O,M);


%save text file:===========================================================
filename=['Experiment','_',datestr(datetime,'ddmmYYYY_HHMMSS')];   %filename
fid = fopen([filename,'.txt'],'w'); %Create new file and set permission to write
fprintf(fid,'Delta T [ms]	16-bit pattern	Board ID,Valve ID;... \n'); %add header
for i = 1:size(col1,1)%Loop through cell array and write to file using a tab delimited specified format
    fprintf(fid,'%s\t%s\t%s\t\n',num2str(col1(i,:)),col2(i,:),col3(i,:));
end
fclose(fid); %close file

%save mat file:============================================================
%create variables which simplify subsequent analysis of the experiments
times.log_start=[];         %extract from TDMS file
times.log_end=[];           %extract from TDMS file
times.trl_start=[];         %extract from TDMS file
times.trl_end=[];           %extract from TDMS file
times.odor=[];              %extract from TDMS file
times.punish=[];            %extract from TDMS file
times.reward=[];            %extract from TDMS file
times.opto=[];              %extract from TDMS file
times.odor_ON=[];           %extract from TDMS file
times.odor_OFF=[];          %extract from TDMS file
times.punish=[];            %extract from TDMS file
times.reward=[];            %extract from TDMS file
times.opto=[];              %extract times from TDMS file
times.olfactomat_delay=[];  %(constant;olfactometer delay)
trial.chemical=[];          %write GUI to input chemcial names from list
trial.odor_ID=nonzeros(bin2dec(col2(:,bits.odor_ID)));
trial.ind=(1:n_trials+pre_block)';
trial.count=ones(n_trials,1);
% trial.reward_ID
% trial.punish_ID

for i=1:n_valves
    trial.count(find(trial.odor_ID==i))=transpose(1:numel(find(trial.odor_ID==i)));
end

signal.breath=[];           %define variables for later data storage
signal.pupil=[];            %define variables for later data storage
trial.vid_index=[];         %define variables for later data storage
n_day = [];
animal_ID = [];

%save mat file with same name as text file
%suffix=['a','b','c','d','e','f'];
%for i=1:length(suffix)
%    save([(filename),'_',(suffix(i))], ...
save(filename,...
        'col1', ...
        'col2', ...
        'col3', ...
        'n_trials', ...
        'n_odors', ...
        'bits', ...
        'times', ...
        'trial', ...
        'signal',...
        'Novelty_task_char',...
        'n_day',...
        'animal_ID');
%end
fprintf('The file "%s" is created \n',filename)
end