import datetime, os, subprocess

from Modules.FileManagers.FileManager import FileManager as FM
from Modules.DataPreparers.PrepPreparer import PrepPreparer as PrP
from Modules.DataPreparers.DepthPreparer import DepthPreparer as DP
from Modules.DataPreparers.ClusterPreparer import ClusterPreparer as CP
from Modules.DataPreparers.MLClusterPreparer import MLClusterPreparer as MLP
from Modules.DataPreparers.FigurePreparer import FigurePreparer as FP

class ProjectPreparer():
	# This class takes in a projectID and runs all the appropriate analysis

	def __init__(self, projectID, workers = None):

		self.projectID = projectID
		if not self._checkProjectID():
			raise Exception(projectID + ' is not valid.')
		self.workers = workers
		self.fileManager = FM(projectID = projectID)

	def _checkProjectID(self):
		projectIDs = subprocess.run(['rclone', 'lsf', self.fileManager.cloudMasterDir], capture_output = True, decoding = 'utf-8')
		if self.projectID + '/' in projectIDs:
			return True
		else:
			return False

	def downloadData(self, dtype):
		self.fileManager.downloadData(dtype)

	def runPrepAnalysis(self):
		self.projFileManager.downloadData('Prep')
		prp_obj = PrP(self.fileManager)
		prp_obj.validateInputData()
		prp_obj.prepData()
		self.createUploadFile(prp_obj.uploads)
		self.createAnalysisUpdate('Prep', prp_obj)
		self.backupAnalysis()
		#self.localDelete()

	def runDepthAnalysis(self):
		dp_obj = DP(self.projFileManager, self.workers)
		dp_obj.validateInputData()
		dp_obj.createSmoothedArray()
		dp_obj.createRGBVideo()
		self.createUploadFile(dp_obj.uploads)
		self.createAnalysisUpdate('Depth', dp_obj)

	def runClusterAnalysis(self, videoIndex):
		cp_obj = CP(self.projFileManager, self.workers, videoIndex)
		cp_obj.validateInputData()
		cp_obj.runClusterAnalysis()
		self.createUploadFile(cp_obj.uploads)
		self.createAnalysisUpdate('Cluster', cp_obj)

	def createAnnotationFrames(self):
		cp_obj = CP(self.projFileManager, self.workers)
		cp_obj.validateInputData()
		cp_obj.createAnnotationFrames()
		self.createUploadFile(cp_obj.uploads)


	def runMLClusterClassifier(self):
		mlc_obj = MLP(self.projFileManager, self.mlFileManager)
		mlc_obj.validateInputData()
		mlc_obj.predictVideoLabels()
		self.createUploadFile(mlc_obj.uploads)
		self.createAnalysisUpdate('MLClassifier', mlc_obj)


	def runMLFishDetection(self):
		pass

	def runFigureCreation(self):
		fc_obj =FP(self.projFileManager)
		fc_obj.validateInputData()
		fc_obj.createDepthFigures()

		self.createUploadFile(fc_obj.uploads)
		self.createAnalysisUpdate('Figures', fc_obj)

	def runObjectLabeling(self):
		self.projFileManager.downloadData('ObjectLabeler')
		lc_obj =LC(self.projFileManager)
		lc_obj.validateInputData()



	def backupAnalysis(self):
		uploadCommands = set()

		uploadFiles = [x for x in os.listdir(self.fileManager.localUploadDir) if 'UploadData' in x]

		for uFile in uploadFiles:
			with open(self.fileManager.localUploadDir + uFile) as f:
				line = next(f)
				for line in f:
					tokens = line.rstrip().split(',')
					tokens[2] = bool(int(tokens[2]))
					uploadCommands.add(tuple(tokens))

		for command in uploadCommands:
			self.fileManager.uploadData(command[0], command[1], command[2])

		for uFile in uploadFiles:
			subprocess.run(['rm', '-rf', self.fileManager.localUploadDir + uFile])

		self.fileManager.uploadData(self.fileManager.localAnalysisLogDir, self.fileManager.cloudAnalysisLogDir, False)
		subprocess.run(['rm', '-rf', self.projFileManager.localMasterDir])

	def localDelete(self):
		subprocess.run(['rm', '-rf', self.projFileManager.localMasterDir])

	def createUploadFile(self, uploads):
		with open(self.fileManager.localUploadDir + 'UploadData_' + str(datetime.datetime.now().timestamp()) + '.csv', 'w') as f:
			print('Local,Cloud,Tar', file = f)
			for upload in uploads:
				print(upload[0] + ',' + upload[1] + ',' + str(upload[2]), file = f)

	def createAnalysisUpdate(self, aType, procObj):
		now = datetime.datetime.now()
		with open(self.fileManager.localAnalysisLogDir + 'AnalysisUpdate_' + str(now.timestamp()) + '.csv', 'w') as f:
			print('ProjectID,Type,Version,Date', file = f)
			print(self.projectID + ',' + aType + ',' + procObj.__version__ + '_' + os.getenv('USER') + ',' + str(now), file= f)
