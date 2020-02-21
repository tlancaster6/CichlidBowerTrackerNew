import platform, sys, os, shutil, datetime, subprocess, gspread, time, socket
from Modules.FileManagers.FileManager import FileManager as FM
from picamera import PiCamera
import numpy as np



import Modules.DataObjects.LogParser as LP

from PIL import Image
from oauth2client.service_account import ServiceAccountCredentials
import matplotlib.image

class CichlidTracker:
    def __init__(self):
        
        # 1: Define valid commands and ignore warnings
        self.commands = ['New', 'Restart', 'Stop', 'Rewrite', 'UploadData', 'LocalDelete', 'Snapshots']
        np.seterr(invalid='ignore')

        # 2: Determine which Kinect is attached (This script can handle v1 or v2 Kinects)
        self._identifyDevice() #Stored in self.device
        self.system = platform.node()

        # 3: Create file manager
        self.fileManager = FM()

        # 4: Download credential files
        self.fileManager.downloadData(self.fileManager.localCredentialSpreadsheet)
        self.fileManager.downloadData(self.fileManager.localCredentialDrive)
        self.credentialSpreadsheet  = self.fileManager.localCredentialSpreadsheet # Rename to make code readable

        # 5: Connect to Google Spreadsheets
        self._authenticateGoogleSpreadSheets() #Creates self.controllerGS
        self._modifyPiGS(error = '')
        
        # 6: Start PiCamera
        self.camera = PiCamera()
        self.camera.resolution = (1296, 972)
        self.camera.framerate = 30
        self.piCamera = 'True'
        
        # 7: Keep track of processes spawned to convert and upload videofiles
        self.processes = [] 

        # 8: Set size of frame
        self.r = (0,0,640,480)

        # 9: Await instructions
        self.monitorCommands()
        
    def __del__(self):
        # Try to close out files and stop running Kinects
        self._modifyPiGS(command = 'None', status = 'Stopped', error = 'UnknownError')
        if self.piCamera:
            if self.camera.recording:
                self.camera.stop_recording()
                self._print('PiCameraStopped: Time=' + str(datetime.datetime.now()) + ', File=Videos/' + str(self.videoCounter).zfill(4) + "_vid.h264")

        try:
            if self.device == 'kinect2':
                self.K2device.stop()
            if self.device == 'kinect':
                freenect.sync_stop()
                freenect.shutdown(self.a)
        except AttributeError:
            pass
        self._closeFiles()

    def monitorCommands(self, delta = 10):
        # This function checks the master Controller Google Spreadsheet to determine if a command was issued (delta = seconds to recheck)
        while True:
            self._identifyTank() #Stored in self.tankID
            command, projectID = self._returnCommand()
            if projectID in ['','None']:
                self._reinstructError('ProjectID must be set')
                time.sleep(delta)
                continue

            print(command + '\t' + projectID)
            if command != 'None':
                self.fileManager.createProjectData(projectID)    
                self.runCommand(command, projectID)
            self._modifyPiGS(status = 'AwaitingCommand')
            time.sleep(delta)

    def runCommand(self, command, projectID):
        # This function is used to run a specific command found int he  master Controller Google Spreadsheet
        self.projectID = projectID

        # Rename files to make code more readable 
        self.projectDirectory = self.fileManager.localProjectDir
        self.loggerFile = self.fileManager.localLogfile
        self.frameDirectory = self.fileManager.localFrameDir
        self.videoDirectory = self.fileManager.localVideoDir
        self.backupDirectory = self.fileManager.localBackupDir

        if command not in self.commands:
            self._reinstructError(command + ' is not a valid command. Options are ' + str(self.commands))
            
        if command == 'Stop':
            
            if self.piCamera:
                if self.camera.recording:
                    self.camera.stop_recording()
                    self._print('PiCameraStopped: Time: ' + str(datetime.datetime.now()) + ',,File: Videos/' + str(self.videoCounter).zfill(4) + "_vid.h264")
                    
                    command = ['python3', 'Modules/processVideo.py', self.videoDirectory + str(self.videoCounter).zfill(4) + '_vid.h264']
                    command += [str(self.camera.framerate[0]), self.projectID]
                    self._print(command)
                    self.processes.append(subprocess.Popen(command))

            try:
                if self.device == 'kinect2':
                    self.K2device.stop()
                if self.device == 'kinect':
                    freenect.sync_stop()
                    freenect.shutdown(self.a)
            except:
                self._print('ErrorStopping kinect')
                
         

            self._closeFiles()

            self._modifyPiGS(command = 'None', status = 'AwaitingCommand')
            return

        if command == 'UploadData':

            self._modifyPiGS(command = 'None')
            self._uploadFiles()
            return
            
        if command == 'LocalDelete':
            if os.path.exists(self.projectDirectory):
                shutil.rmtree(self.projectDirectory)
            self._modifyPiGS(command = 'None', status = 'AwaitingCommand')
            return
        
        self._modifyPiGS(command = 'None', status = 'Running', error = '')


        if command == 'New':
            # Project Directory should not exist. If it does, report error
            if os.path.exists(self.projectDirectory):
                self._reinstructError('New command cannot be run if ouput directory already exists. Use Rewrite or Restart')

        if command == 'Rewrite':
            if os.path.exists(self.projectDirectory):
                shutil.rmtree(self.projectDirectory)
            os.mkdir(self.projectDirectory)
            #subprocess.call([self.dropboxScript, '-f', self.credentialDropbox, 'delete', projectID], stdout = open(self.projectDirectory + 'DropboxDeleteOut.txt', 'a'), stderr = open(self.projectDirectory + 'DropboxDeleteError.txt', 'a'))
            
        if command in ['New','Rewrite']:
            self.masterStart = datetime.datetime.now()
            if command == 'New':
                os.mkdir(self.projectDirectory)
            os.mkdir(self.frameDirectory)
            os.mkdir(self.videoDirectory)
            os.mkdir(self.backupDirectory)
            #self._createDropboxFolders()
            self.frameCounter = 1
            self.videoCounter = 1

        if command == 'Restart':
            logObj = LP.LogParser(self.loggerFile)
            self.masterStart = logObj.master_start
            #self.r = logObj.bounding_shape
            self.frameCounter = logObj.lastFrameCounter + 1
            self.videoCounter = logObj.lastVideoCounter + 1
            if self.system != logObj.system or self.device != logObj.device or self.piCamera != logObj.camera:
                self._reinstructError('Restart error. System, device, or camera does not match what is in logfile')
                
        self.lf = open(self.loggerFile, 'a')
        self._modifyPiGS(start = str(self.masterStart))

        if command in ['New', 'Rewrite']:
            self._print('MasterStart: System: '+self.system + ',,Device: ' + self.device + ',,Camera: ' + str(self.piCamera) + ',,Uname: ' + str(platform.uname()) + ',,TankID: ' + self.tankID + ',,ProjectID: ' + self.projectID)
            self._print('MasterRecordInitialStart: Time: ' + str(self.masterStart))
            self._print('PrepFiles: FirstDepth: PrepFiles/FirstDepth.npy,,LastDepth: PrepFiles/LastDepth.npy,,PiCameraRGB: PiCameraRGB.jpg,,DepthRGB: DepthRGB.jpg')
            picamera_settings = {'AnalogGain': str(self.camera.analog_gain), 'AWB_Gains': str(self.camera.awb_gains), 
                                'AWB_Mode': str(self.camera.awb_mode), 'Brightness': str(self.camera.brightness), 
                                'ClockMode': str(self.camera.clock_mode), 'Contrast': str(self.camera.contrast),
                                'Crop': str(self.camera.crop),'DigitalGain': str(self.camera.digital_gain),
                                'ExposureCompensation': str(self.camera.exposure_compensation),'ExposureMode': str(self.camera.exposure_mode),
                                'ExposureSpeed': str(self.camera.exposure_speed),'FrameRate': str(self.camera.framerate),
                                'ImageDenoise': str(self.camera.image_denoise),'MeterMode': str(self.camera.meter_mode),
                                'RawFormat': str(self.camera.raw_format), 'Resolution': str(self.camera.resolution),
                                'Saturation': str(self.camera.saturation),'SensorMode': str(self.camera.sensor_mode),
                                'Sharpness': str(self.camera.sharpness),'ShutterSpeed': str(self.camera.shutter_speed),
                                'VideoDenoise': str(self.camera.video_denoise),'VideoStabilization': str(self.camera.video_stabilization)}
            self._print('PiCameraSettings: ' + ',,'.join([x + ': ' + picamera_settings[x] for x in sorted(picamera_settings.keys())]))
            #self._createROI(useROI = False)

        else:
            self._print('MasterRecordRestart: Time: ' + str(datetime.datetime.now()))

            
        # Start kinect
        self._start_kinect()
        
        # Diagnose speed
        self._diagnose_speed()

        # Capture data
        self.captureFrames()
    
    def captureFrames(self, frame_delta = 5, background_delta = 5, max_frames = 20, stdev_threshold = 20):

        current_background_time = datetime.datetime.now()
        current_frame_time = current_background_time + datetime.timedelta(seconds = 60 * frame_delta)

        command = ''
        
        while True:
            self._modifyPiGS(command = 'None', status = 'Running', error = '')
            # Grab new time
            now = datetime.datetime.now()
            
            # Fix camera if it needs to be
            if self.piCamera:
                if self._video_recording() and not self.camera.recording:
                    self.camera.capture(self.videoDirectory + str(self.videoCounter).zfill(4) + "_pic.jpg")
                    self.camera.start_recording(self.videoDirectory + str(self.videoCounter).zfill(4) + "_vid.h264", bitrate=7500000)
                    self._print('PiCameraStarted: FrameRate: ' + str(self.camera.framerate) + ',,Resolution: ' + str(self.camera.resolution) + ',,Time: ' + str(datetime.datetime.now()) + ',,VideoFile: Videos/' + str(self.videoCounter).zfill(4) + '_vid.h264,,PicFile: Videos/' + str(self.videoCounter).zfill(4) + '_pic.jpg')
                elif not self._video_recording() and self.camera.recording:
                    self.camera.stop_recording()
                    self._print('PiCameraStopped: Time: ' + str(datetime.datetime.now()) + ',, File: Videos/' + str(self.videoCounter).zfill(4) + "_vid.h264")
                    #self._print(['rclone', 'copy', self.videoDirectory + str(self.videoCounter).zfill(4) + "_vid.h264"])
                    command = ['python3', 'Modules/processVideo.py', self.videoDirectory + str(self.videoCounter).zfill(4) + '_vid.h264']
                    command += [str(self.camera.framerate[0]), self.projectID]
                    self._print(command)
                    self.processes.append(subprocess.Popen(command))
                    self.videoCounter += 1

            # Capture a frame and background if necessary
            
            if now > current_background_time:
                if command == 'Snapshots':
                    out = self._captureFrame(current_frame_time, max_frames = max_frames, stdev_threshold = stdev_threshold, snapshots = True)
                else:
                    out = self._captureFrame(current_frame_time, max_frames = max_frames, stdev_threshold = stdev_threshold)
                if out is not None:
                    current_background_time += datetime.timedelta(seconds = 60 * background_delta)
                subprocess.Popen(['python3', 'Modules/DriveUpdater.py', self.loggerFile])
            else:
                if command == 'Snapshots':
                    out = self._captureFrame(current_frame_time, max_frames = max_frames, stdev_threshold = stdev_threshold, snapshots = True)
                else:    
                    out = self._captureFrame(current_frame_time, max_frames = max_frames, stdev_threshold = stdev_threshold)
            current_frame_time += datetime.timedelta(seconds = 60 * frame_delta)

            self._modifyPiGS(status = 'Running')

            
            # Check google doc to determine if recording has changed.
            try:
                command, projectID = self._returnCommand()
            except KeyError:
                continue                
            if command != 'None':
                if command == 'Snapshots':
                    self._modifyPiGS(command = 'None', status = 'Writing Snapshots')
                    continue
                else:
                    break
            else:
                self._modifyPiGS(error = '')

    def _authenticateGoogleSpreadSheets(self):
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets"
        ]
        credentials = ServiceAccountCredentials.from_json_keyfile_name(self.credentialSpreadsheet, scope)
        for i in range(0,3): # Try to autheticate three times before failing
            try:
                gs = gspread.authorize(credentials)
            except:
                continue
            try:
                self.controllerGS = gs.open('Controller')
                pi_ws = self.controllerGS.worksheet('RaspberryPi')
            except:
                continue
            try:
                headers = pi_ws.row_values(1)
            except:
                continue
            column = headers.index('RaspberryPiID') + 1
            try:
                pi_ws.col_values(column).index(platform.node())
                return True
            except ValueError:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                s.close()
                try:
                    pi_ws.append_row([platform.node(),ip,'','','','','','None','Stopped','Error: Awaiting assignment of TankID',str(datetime.datetime.now())])
                except:
                    continue
                return True
            except:
                continue    
            time.sleep(2)
        return False
            
    def _identifyDevice(self):
        try:
            global freenect
            import freenect
            self.a = freenect.init()
            if freenect.num_devices(self.a) == 0:
                kinect = False
            elif freenect.num_devices(self.a) > 1:
                self._initError('Multiple Kinect1s attached. Unsure how to handle')
            else:
                kinect = True
        except ImportError:
            kinect = False

        try:
            global FN2
            import pylibfreenect2 as FN2
            if FN2.Freenect2().enumerateDevices() == 1:
                kinect2 = True
            elif FN2.Freenect2().enumerateDevices() > 1:
                self._initError('Multiple Kinect2s attached. Unsure how to handle')
            else:
                kinect2 = False
        except ImportError:
            kinect2 = False

        if kinect and kinect2:
            self._initError('Kinect1 and Kinect2 attached. Unsure how to handle')
        elif not kinect and not kinect2:
            self._initError('No depth sensor  attached')
        elif kinect:
            self.device = 'kinect'
        else:
            self.device = 'kinect2'
       
    def _identifyTank(self):
        while True:
            self._authenticateGoogleSpreadSheets() # link to google drive spreadsheet stored in self.controllerGS 
            pi_ws = self.controllerGS.worksheet('RaspberryPi')
            headers = pi_ws.row_values(1)
            raPiID_col = headers.index('RaspberryPiID') + 1
            for i in range(5):
                try:
                    row = pi_ws.col_values(raPiID_col).index(platform.node()) + 1
                    break
                except:
                    continue
            col = headers.index('TankID')
            if pi_ws.row_values(row)[col] not in ['None','']:
                self.tankID = pi_ws.row_values(row)[col]
                for i in range(5):
                    try:
                        self._modifyPiGS(capability = 'Device=' + self.device + ',Camera=' + str(self.piCamera), status = 'AwaitingCommand')
                        return
                    except:
                        continue
                return
            else:
                self._modifyPiGS(error = 'Awaiting assignment of TankID')
                time.sleep(5)
        
    def _initError(self, message):
        try:
            self._modifyPiGS(command = 'None', status = 'Stopped', error = 'InitError: ' + message)
        except:
            pass
        self._print('InitError: ' + message)
        raise TypeError
            
    def _reinstructError(self, message):
        self._modifyPiGS(command = 'None', status = 'AwaitingCommands', error = 'InstructError: ' + message)

        # Update google doc to indicate error
        self.monitorCommands()
 
    def _print(self, text):
        try:
            print(text, file = self.lf, flush = True)
        except:
            pass
        print(text, file = sys.stderr, flush = True)

    def _returnRegColor(self, crop = True):
        # This function returns a registered color array
        if self.device == 'kinect':
            out = freenect.sync_get_video()[0]
            
        elif self.device == 'kinect2':
            undistorted = FN2.Frame(512, 424, 4)
            registered = FN2.Frame(512, 424, 4)
            frames = self.listener.waitForNewFrame()
            color = frames["color"]
            depth = frames["depth"]
            self.registration.apply(color, depth, undistorted, registered, enable_filter=False)
            reg_image =  registered.asarray(np.uint8)[:,:,0:3].copy()
            self.listener.release(frames)
            out = reg_image

        if crop:
            return out[self.r[1]:self.r[1]+self.r[3], self.r[0]:self.r[0]+self.r[2]]
        else:
            return out
            
    def _returnDepth(self):
        # This function returns a float64 npy array containing one frame of data with all bad data as NaNs
        if self.device == 'kinect':
            data = freenect.sync_get_depth()[0].astype('Float64')
            data[data == 2047] = np.nan # 2047 indicates bad data from Kinect 
            return data[self.r[1]:self.r[1]+self.r[3], self.r[0]:self.r[0]+self.r[2]]
        
        elif self.device == 'kinect2':
            frames = self.listener.waitForNewFrame(timeout = 1000)
            output = frames['depth'].asarray()
            self.listener.release(frames)
            return output[self.r[1]:self.r[1]+self.r[3], self.r[0]:self.r[0]+self.r[2]]

    def _returnCommand(self):
        if not self._authenticateGoogleSpreadSheets():
            raise KeyError
            # link to google drive spreadsheet stored in self.controllerGS
        while True:
            try:
                pi_ws = self.controllerGS.worksheet('RaspberryPi')
                headers = pi_ws.row_values(1)
                piIndex = pi_ws.col_values(headers.index('RaspberryPiID') + 1).index(platform.node())
                command = pi_ws.col_values(headers.index('Command') + 1)[piIndex]
                projectID = pi_ws.col_values(headers.index('ProjectID') + 1)[piIndex]
                return command, projectID
            except gspread.exceptions.RequestError:
                continue

    def _modifyPiGS(self, start = None, command = None, status = None, IP = None, capability = None, error = None):
        while not self._authenticateGoogleSpreadSheets(): # link to google drive spreadsheet stored in self.controllerGS
            continue
        try:
            pi_ws = self.controllerGS.worksheet('RaspberryPi')
            headers = pi_ws.row_values(1)
            row = pi_ws.col_values(headers.index('RaspberryPiID')+1).index(platform.node()) + 1
            if start is not None:
                column = headers.index('MasterStart') + 1
                pi_ws.update_cell(row, column, start)
            if command is not None:
                column = headers.index('Command') + 1
                pi_ws.update_cell(row, column, command)
            if status is not None:
                column = headers.index('Status') + 1
                pi_ws.update_cell(row, column, status)
            if error is not None:
                column = headers.index('Error') + 1
                pi_ws.update_cell(row, column, error)
            if IP is not None:
                column = headers.index('IP')+1
                pi_ws.update_cell(row, column, IP)
            if capability is not None:
                column = headers.index('Capability')+1
                pi_ws.update_cell(row, column, capability)
            column = headers.index('Ping') + 1
            pi_ws.update_cell(row, column, str(datetime.datetime.now()))
        except gspread.exceptions.RequestError as e:
            self._print('GoogleError: Time: ' + str(datetime.datetime.now()) + ',,Error: ' + str(e))
        except TypeError:
            self._print('GoogleError: Time: ' + str(datetime.datetime.now()) + ',,Error: Unknown. Gspread does not handle RequestErrors properly')
    
    def _video_recording(self):
        if datetime.datetime.now().hour >= 8 and datetime.datetime.now().hour <= 18:
            return True
        else:
            return False
            
    def _start_kinect(self):
        if self.device == 'kinect':
            freenect.sync_get_depth() #Grabbing a frame initializes the device
            freenect.sync_get_video()

        elif self.device == 'kinect2':
            # a: Identify pipeline to use: 1) OpenGL, 2) OpenCL, 3) CPU
            try:
                self.pipeline = FN2.OpenCLPacketPipeline()
            except:
                try:
                    self.pipeline = FN2.OpenGLPacketPipeline()
                except:
                    self.pipeline = FN2.CpuPacketPipeline()
            self._print('PacketPipeline: ' + type(self.pipeline).__name__)

            # b: Create and set logger
            self.logger = FN2.createConsoleLogger(FN2.LoggerLevel.NONE)
            FN2.setGlobalLogger(self.logger)

            # c: Identify device and create listener
            self.fn = FN2.Freenect2()
            serial = self.fn.getDeviceSerialNumber(0)
            self.K2device = self.fn.openDevice(serial, pipeline=self.pipeline)

            self.listener = FN2.SyncMultiFrameListener(
                FN2.FrameType.Color | FN2.FrameType.Depth)
            # d: Register listeners
            self.K2device.setColorFrameListener(self.listener)
            self.K2device.setIrAndDepthFrameListener(self.listener)
            # e: Start device and create registration
            self.K2device.start()
            self.registration = FN2.Registration(self.K2device.getIrCameraParams(), self.K2device.getColorCameraParams())
    
    def _diagnose_speed(self, time = 10):
        print('Diagnosing speed for ' + str(time) + ' seconds.', file = sys.stderr)
        delta = datetime.timedelta(seconds = time)
        start_t = datetime.datetime.now()
        counter = 0
        while True:
            depth = self._returnDepth()
            counter += 1
            if datetime.datetime.now() - start_t > delta:
                break
        #Grab single snapshot of depth and save it
        depth = self._returnDepth()
        np.save(self.projectDirectory +'Frames/FirstFrame.npy', depth)

        #Grab a bunch of depth files to characterize the variability
        data = np.zeros(shape = (50, self.r[3], self.r[2]))
        for i in range(0, 50):
            data[i] = self._returnDepth()
            
        counts = np.count_nonzero(~np.isnan(data), axis = 0)
        std = np.nanstd(data, axis = 0)
        np.save(self.projectDirectory +'Frames/FirstDataCount.npy', counts)
        np.save(self.projectDirectory +'Frames/StdevCount.npy', std)
         
        self._print('DiagnoseSpeed: Rate: ' + str(counter/time))

        self._print('FirstFrameCaptured: FirstFrame: Frames/FirstFrame.npy,,GoodDataCount: Frames/FirstDataCount.npy,,StdevCount: Frames/StdevCount.npy')
    
    def _captureFrame(self, endtime, max_frames = 40, stdev_threshold = 25, snapshots = False):
        # Captures time averaged frame of depth data
        
        sums = np.zeros(shape = (self.r[3], self.r[2]))
        n = np.zeros(shape = (self.r[3], self.r[2]))
        stds = np.zeros(shape = (self.r[3], self.r[2]))
        
        current_time = datetime.datetime.now()
        if current_time >= endtime:
            return

        counter = 1
        while True:
            all_data = np.empty(shape = (int(max_frames), self.r[3], self.r[2]))
            all_data[:] = np.nan
            for i in range(0, max_frames):
                all_data[i] = self._returnDepth()
                current_time = datetime.datetime.now()

                if snapshots:
                    self._print('SnapshotCaptured: NpyFile: Frames/Snapshot_' + str(counter).zfill(6) + '.npy,,Time: ' + str(current_time)  + ',,GP: ' + str(np.count_nonzero(~np.isnan(all_data[i]))))
                    np.save(self.projectDirectory +'Frames/Snapshot_' + str(counter).zfill(6) + '.npy', all_data[i])

                
                counter += 1

                if current_time >= endtime:
                    break
              
            med = np.nanmedian(all_data, axis = 0)
            med[np.isnan(med)] = 0
            std = np.nanstd(all_data, axis = 0)
            med[np.isnan(std)] = 0
            med[std > stdev_threshold] = 0
            std[std > stdev_threshold] = 0
            counts = np.count_nonzero(~np.isnan(all_data), axis = 0)
            med[counts < 3] = 0
            std[counts < 3] = 0

            
            sums += med
            stds += std

            med[med > 1] = 1
            n += med
            current_time = datetime.datetime.now()
            if current_time >= endtime:
                break

        avg_med = sums/n
        avg_std = stds/n
        color = self._returnRegColor()                        
        num_frames = int(max_frames*(n.max()-1) + i + 1)
        
        self._print('FrameCaptured: NpyFile: Frames/Frame_' + str(self.frameCounter).zfill(6) + '.npy,,PicFile: Frames/Frame_' + str(self.frameCounter).zfill(6) + '.jpg,,Time: ' + str(endtime)  + ',,NFrames: ' + str(num_frames) + ',,AvgMed: '+ '%.2f' % np.nanmean(avg_med) + ',,AvgStd: ' + '%.2f' % np.nanmean(avg_std) + ',,GP: ' + str(np.count_nonzero(~np.isnan(avg_med))))
        
        np.save(self.projectDirectory +'Frames/Frame_' + str(self.frameCounter).zfill(6) + '.npy', avg_med)
        matplotlib.image.imsave(self.projectDirectory+'Frames/Frame_' + str(self.frameCounter).zfill(6) + '.jpg', color)
        self.frameCounter += 1

        return avg_med

            
    def _uploadFiles(self):
        self._modifyPiGS(status = 'Finishing converting and uploading of videos')
        for p in self.processes:
            p.communicate()
        
        for movieFile in os.listdir(self.videoDirectory):
            if '.h264' in movieFile:
                command = ['python3', 'Modules/processVideo.py', movieFile]
                command += [str(self.camera.framerate[0]), self.projectID]
                self._print(command)
                self.processes.append(subprocess.Popen(command))

        for p in self.processes:
            p.communicate()

        self._modifyPiGS(status = 'Creating prep files')

        # Move files around as appropriate
        prepDirectory = self.projectDirectory + 'PrepFiles/'
        shutil.rmtree(prepDirectory) if os.path.exists(prepDirectory) else None
        os.makedirs(prepDirectory)

        lp = LP.LogParser(self.loggerFile)

        self.frameCounter = lp.lastFrameCounter + 1

        videoObj = [x for x in lp.movies if x.startTime.hour >= 8 and x.startTime.hour <= 20][0]
        subprocess.call(['cp', self.projectDirectory + videoObj.pic_file, prepDirectory + 'PiCameraRGB.jpg'])

        subprocess.call(['cp', self.projectDirectory + lp.movies[-1].pic_file, prepDirectory + 'LastPiCameraRGB.jpg'])

        # Find depthfile that is closest to the video file time
        depthObj = [x for x in lp.frames if x.time > videoObj.startTime][0]


        subprocess.call(['cp', self.projectDirectory + depthObj.pic_file, prepDirectory + 'DepthRGB.jpg'])

        if not os.path.isdir(self.frameDirectory):
            self._modifyPiGS(status = 'Error: ' + self.frameDirectory + ' does not exist.')
            return

        subprocess.call(['cp', self.frameDirectory + 'Frame_000001.npy', prepDirectory + 'FirstDepth.npy'])
        subprocess.call(['cp', self.frameDirectory + 'Frame_' + str(self.frameCounter-1).zfill(6) + '.npy', prepDirectory + 'LastDepth.npy'])
        
        try:
            self._modifyPiGS(status = 'Uploading data to cloud')
            self.fileManager.uploadData(self.frameDirectory, tarred = True)
            self.fileManager.uploadData(prepDirectory)
            self.fileManager.uploadData(self.videoDirectory)
            self.fileManager.uploadData(self.loggerFile)
            self._modifyPiGS(status = 'UploadSuccessful, ready for delete')

        except:
            self._modifyPiGS(status = 'UploadFailed, Need to rerun')
        
    def _closeFiles(self):
       try:
            self._print('MasterRecordStop: ' + str(datetime.datetime.now()))
            self.lf.close()
       except AttributeError:
           pass
       try:
           if self.system == 'mac':
               self.caff.kill()
       except AttributeError:
           pass

