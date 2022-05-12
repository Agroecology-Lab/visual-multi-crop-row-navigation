#!/usr/bin/env python3

import scipy
import cv2 as cv
import numpy as np
from scipy.signal import find_peaks
from matplotlib import pyplot as plt
from movingVariance  import movingStd
import itertools
from cv_bridge import CvBridge
class featureExtractor:
    def __init__(self, windowProp, roiProp, fexProp):
        """# class FeatureExtractor to extract the the line Features: bottom point, angle

        Args:
            windowProp (_type_): _description_
            roiProp (_type_): _description_
            fexProp (_type_): _description_
        """
        # parameters
        self.img = []
        self.processedIMG = []
        self.linesTracked = []
        self.windowPoints = []
        self.windowLocations = []
        self.pointsT = 0
        self.pointsB = 0
        self.lineFound = False
        self.isInitialized = False
        self.bushy = False

        self.roiProp = roiProp
        self.windowProp = windowProp
        
        # features
        self.topIntersect = []
        self.botIntersect = []
        self.P = np.array([])
        self.allLineStart = []
        self.allLineEnd = []
        self.rowAngles = []
        self.mainLine_up = np.array([])
        self.mainLine_down = np.array([])
        
        # window parameters
        self.offsB = 0
        self.offsT = 0
        self.winSize = None       
        self.winRatio = 0.4 # scales the top of the window
        self.winMinWidth = None

        self.min_contour_area = fexProp["min_contour_area"]
        self.max_coutour_height = fexProp["max_coutour_height"]
        
        # search parameters
        self.steps = 50
        self.turnWindowWidth = 50
        self.winSweepStart = []
        self.winSweepEnd = []

        self.count = 0

        self.smoothSize = 5

        # counter of newly detected rows in switching process
        self.newDetectedRows = 0

        self.numVec= np.zeros((300,1))
        self.meanVec = np.zeros((300,1))

        # crop row recognition Difference thersholds
        self.max_matching_dif_features = 100      
        self.min_matching_dif_features = 0          
        # Threshold for keypoints
        self.matching_keypoints_th = 10
        # if there is no plant in the image
        self.noPlantsSeen = False
        self.nrNoPlantsSeen = 0
           
    def setImgProp(self, img):
        """function to set the image and its properties

        Args:
            img (_type_): _description_
        """
        if self.img is not None or len(self.img) != 0:
            self.img = img
            self.processedIMG = self.img.copy()
            self.imgHeight, self.imgWidth, self.imgChannels = self.img.shape
            
            if self.winSize is None:
                self.winSize = self.windowProp["winSize"] # 8 for small plants, /5 for big plants
                self.winMinWidth = self.windowProp["winMinWidth"]
                self.winSweepStart = self.windowProp["winSweepStart"]
                self.winSweepEnd = self.windowProp["winSweepEnd"]
        else:
            print("Invalid image")
        
    def initialize(self, bushy=False):
        """funciton to initialize the feature extractor

        Args:
            bushy (bool, optional): _description_. Defaults to False.
        """
        # if crop canopy type is busshy like Coriander
        self.bushy = bushy
        # check if current image is not Empty or None
        if self.img is None or len(self.img) == 0:
            print("#[ERR] feEx - Image is None, Check Topics or the Sensor !")
        else:
            print("#[INF] feEx - Searching for crop rows, please wait...")
            # steps create linspace
            winidx = np.linspace(self.winSweepStart, self.winSweepEnd, self.steps)
            # search for lines at given window locations using LQ-Adjustment
            lParams, winLoc, _= self.getLinesAtWindow(winidx)
            # if any feature is observed during the scan
            if len(lParams) != 0:
                np.set_printoptions(suppress=True)
                # compute moving standard deviation
                movVarM = movingStd(lParams[:,1])
                # find positive an negative peaks of the signal
                peaksNeg, peaksPos = self.findPicks(movVarM, 0.5)
                # locate best lines 
                self.findBestLines(peaksPos, peaksNeg, movVarM, lParams, winLoc)
                # if there is any proper line to follow
                if len(self.paramsBest) != 0:
                    # get intersections of the lines with the image borders and 
                    # average these to get the main line
                    avgLine = np.mean(np.c_[self.lineIntersectIMG(self.paramsBest[:,1], self.paramsBest[:,0])],axis=0)
                    self.linesTracked = self.paramsBest
                    self.top_x = avgLine[0]
                    self.bottom_x = avgLine[1]
                    #  get AvgLine in image cords
                    self.mainLine_up, self.mainLine_down = self.getLineInImage(avgLine)
                    # main features
                    self.P = self.cameraToImage([self.bottom_x, self.imgHeight]) 
                    self.ang = self.computeTheta(self.mainLine_up, self.mainLine_down)
                    # set parameters indicating a successfull initialization
                    self.isInitialized = True
                    self.lineFound = True
                    print('--- Initialisation completed ---')
                    print('  # Crop Rows:', len( self.paramsBest))
                    print('  Window positions:', self.windowLocations)  
                else:
                    print('--- Initialisation failed - No lines detected due to peak detection ---')
                    self.lineFound = False
                    self.processedIMG = self.img                  
            else:
                print('--- Initialisation failed - No lines detected by sweeping window ---')
                self.lineFound = False
                self.processedIMG = self.img

    def findPicks(self, movVarM, prominence=0.5):
        # find negative peaks (=least std, most stable line)                           
        peaksNeg, _ = find_peaks(-movVarM)
        # find positive peaks (=least stable lines = crop row transition)
        peaksPos, _ = find_peaks(movVarM, prominence=0.5)
        return peaksNeg, peaksPos

    def findBestLines(self, peaksPos, peaksNeg, movVarM, lParams, winLoc):
        # if multiple lines
        if len(peaksPos) != 0 and len(peaksNeg) != 0 and len(movVarM) != 0:    
            # best line one each side of the crop row transition
            self.paramsBest = np.zeros((len(peaksPos)+1, 2))
            self.windowLocations = np.zeros((len(peaksPos)+1, 1))
            # get lines at each side of a positive peak (= crop row
            # transition and select the best)
            try:
                lidx = 0
                # every peak stands for 2 croprows
                for k in range(0, len(peaksPos) + 1):
                    # first peak
                    if k == 0:
                        lines = peaksNeg[peaksNeg<peaksPos[k]]
                    else:
                        # second to last-1
                        if k < len(peaksPos):
                            linestmp = peaksNeg[peaksNeg<peaksPos[k]]
                            lines = linestmp[linestmp>peaksPos[k-1]]
                        # last peak
                        else:
                            lines = peaksNeg[peaksNeg>peaksPos[k-1]]
                     
                    if len(lines) != 0:
                        # best line (measured by variance)
                        bestLine = np.where(movVarM[lines]==np.min(movVarM[lines]))[0][0]
                        # parameters of that line
                        self.paramsBest[lidx,:] = lParams[lines[bestLine]]
                        # location of the window which led to this line
                        if winLoc[lines[bestLine]] != [0]:
                            self.windowLocations[lidx,:] = winLoc[lines[bestLine]]
                            lidx += 1
            except:
                # fallback: just take all negative peaks
                self.paramsBest = lParams[peaksNeg]
                self.windowLocations = np.mean(winLoc[peaksNeg])
                
        # if there are no positive peaks but negative ones: no crop 
        # row transition -> there might be just one line
        elif len(peaksPos) == 0 and len(peaksNeg) != 0 and len(movVarM) != 0:
            lines = peaksNeg
            bestLine = np.where(movVarM[lines]==np.min(movVarM[lines]))[0][0]
            
            self.paramsBest = np.zeros((1, 2))
            self.windowLocations = np.zeros((1, 1))
            
            self.paramsBest[0,:] = lParams[lines[bestLine]]
            self.windowLocations[0] = winLoc[lines[bestLine]]        
        else:
            self.paramsBest = []
            self.windowLocations = []

    def updateLinesAtWindows(self):
        """function to get lines at given window locations 

        Returns:
            _type_: _description_
        """
        self.rowAngles = []
        # if the feature extractor is initalized
        if len(self.windowLocations) != 0:    
            # get the lines at windows defined through previous found lines
            lParams, _, winMean = self.getLinesAtWindow(self.windowLocations)      
            # if 'all' lines are found by 'self.getLinesAtWindow'
            if len(lParams) >= len(self.windowLocations):
                # location is always the left side of the window
                self.windowLocations = winMean-self.winSize/2
                # the line parameters are the new tracked lines (for the next step)
                self.linesTracked = lParams
                # average image intersections of all found lines
                avgLine = np.mean(np.c_[self.lineIntersectIMG(lParams[:,1], lParams[:,0])],axis=0)
                # get intersection points
                self.linesTracked = self.paramsBest
                self.top_x = avgLine[0]
                self.bottom_x = avgLine[1]
                #  get AvgLine in image cords
                self.mainLine_up, self.mainLine_down = self.getLineInImage(avgLine)
                # compute all intersections between the image and each line
                allLineIntersect = np.c_[self.lineIntersectWin(lParams[:,1], lParams[:,0])]
                # store start and end points of lines - mainly for plotting
                self.allLineStart = np.c_[allLineIntersect[:,0], self.imgHeight - self.offsB * np.ones((len(lParams),1))]
                self.allLineEnd = np.c_[allLineIntersect[:,1], self.offsT * np.ones((len(lParams),1))]

                for line in range(len(self.allLineEnd)):
                    ang = self.computeTheta(self.allLineStart[line], self.allLineEnd[line])
                    # print(ang, self.allLineStart[line], self.allLineEnd[line])
                    self.rowAngles.append(ang)
                
                # main features
                self.P = self.cameraToImage([self.bottom_x, self.imgHeight]) 
                self.ang = self.computeTheta(self.mainLine_up, self.mainLine_down)
                
                print('Tracking lines at window positions', self.windowLocations.tolist(), avgLine, self.P, self.ang)
                
            # if there are less lines than window positions
            else:
                print("Lost at least one line")
                self.lineFound = False
        else:
            print('Running .initialize() first!')
            self.initialize()
        return self.lineFound, self.mask
    
    def getLineInImage(self,line):
        up = [line[1], 0]
        down = [line[0], self.imgHeight]
        return up, down

    def isOnRightSide(self, x, y, xy0, xy1):
        x0, y0 = xy0
        x1, y1 = xy1
        a = float(y1 - y0)
        b = float(x0 - x1)
        c = - a*x0 - b*y0
        return a*x + b*y + c >= 0

    def testPoint(self, x, y, vertices):
        num_vert = len(vertices)
        is_right = [self.isOnRightSide(x, y, vertices[i], vertices[(i + 1) % num_vert]) for i in range(num_vert)]
        all_left = not any(is_right)
        all_right = all(is_right)
        return all_left or all_right

    def getLinesAtWindow(self, winidx):
        """function to get lines at given window locations 

        Args:
            winidx (_type_): _description_

        Returns:
            _type_: lines in image
        """
        # greenidx, contour center points
        rawdata,x,y = self.getImageData()

        # initialization
        lParams = np.zeros((len(winidx),2))
        winLoc = np.zeros((len(winidx),1))
        winMean = np.zeros((len(winidx),1))                          
        self.windowPoints = np.zeros((len(winidx),6))
        var = None
        
        # counting the points in the top and bottom half
        self.pointsB = 0
        self.pointsT = 0

        # for all windows
        for i in range(0, len(winidx)):
            # define window
            if self.isInitialized and len(self.linesTracked) != 0:
                # If the feature extractor is already initalized, winidx is 
                # basically being ignored.
                # Instead of centering the window around winidx[i], the window
                # is defined by a parallelogram, centered around the previous
                # found line.
                
                # get x values, where the top and bottom of the window intersect
                # with the previous found line
                win_intersect = self.lineIntersectWin(self.linesTracked[i,1], self.linesTracked[i,0])
                
                # window corner points are left and right of these
                xWinBL = max(win_intersect[0] - self.winSize/2, 0)
                xWinBR = min(xWinBL + self.winSize, self.imgWidth)   
                xWinTL = max(win_intersect[1] - self.winSize/2 * self.winRatio, 0)
                xWinTR = min(xWinTL + self.winSize * self.winRatio, self.imgWidth)
                           
                # y untouched (like normal rectangular)
                yWinB = self.offsB
                yWinT = self.imgHeight-self.offsT
                
                # store the corner points
                self.windowPoints[i,:] = [xWinBL, xWinTL, xWinTR, xWinBR, self.imgHeight-yWinB, self.imgHeight-yWinT]
                
            else:        
                # window is centered around winidx[i]
                xWinBL = winidx[i]
                xWinBR = winidx[i]+self.winSize
                xWinTL = xWinBL
                xWinTR = xWinBR
                yWinB = self.offsB
                yWinT = self.imgHeight-self.offsT
                
                # store the corner points
                self.windowPoints[i,:] = [xWinBL, xWinTL, xWinTR, xWinBR, self.imgHeight-yWinB, self.imgHeight-yWinT]
         
            ptsIn = np.zeros((len(x),2))

            # get points inside window
            for m in range(0, len(x)):
                # checking #points in upper/lower half of the image
                if y[m] > self.imgHeight/2:
                    self.pointsT += 1
                else:
                    self.pointsB += 1
                
                # different query needed, if the window is a parallelogram
                if self.isInitialized and len(self.linesTracked) != 0 and self.linesTracked is not None:
                    # get the x value of the tracked line of the previous step
                    # at the y value of the query point
                    lXAtY = self.lineIntersectY(self.linesTracked[i,1], self.linesTracked[i,0], y[m])
                    
                    # window height 
                    winH = yWinT-yWinB
                    
                    # window width
                    winW = self.winSize - y[m]/winH*self.winSize*(1-self.winRatio)
                    
                    # set up x constraint for query point
                    lXAtY_min = lXAtY - winW/2
                    lXAtY_max = lXAtY + winW/2

                    # print("winH:", winH, "winW:",  winW , "lXAtY_min:", lXAtY_min, "lXAtY_max:", lXAtY_max)
                    
                    # test if the query point is inside the window
                    ptInWin = x[m]>lXAtY_min and x[m]<lXAtY_max and y[m]>yWinB and y[m] < yWinT
                                        
                # rectangular window
                else:
                    ptInWin = x[m]>xWinBL and x[m]<xWinBR and y[m]>yWinB and y[m] < yWinT

                # if the point is inside the window, add it to ptsIn
                if ptInWin: 
                    ptsIn[m,:] = rawdata[:,m]
                    
            # remove zero rows
            ptsIn = ptsIn[~np.all(ptsIn == 0, axis=1)]

            # line fit
            if len(ptsIn) > 2:
                # flipped points
                ptsFlip = np.flip(ptsIn, axis=1)

                xM, xB = self.getLineRphi(ptsFlip)
                t_i, b_i = self.lineIntersectIMG(xM, xB)                   
                l_i, r_i = self.lineIntersectSides(xM, xB)

                # time.sleep(10)
                # if the linefit does not return None and the line-image intersections
                # are within the image bounds
                if xM is not None and b_i >= 0 and b_i <= self.imgWidth:
                    lParams[i, :] = [xB, xM]
                    
                    # store the window location, which generated these line
                    # parameters
                    if self.isInitialized == False:
                        winLoc[i] = winidx[i]
        
                    # mean x value of all points used for fitting the line
                    winMean[i] = np.median(ptsIn,axis=0)[0]

        
        # if the feature extractor is initalized, adjust the window
        # size with the variance of the line fit
        if self.isInitialized and var is not None:
            self.winSize = max(3*var, self.winMinWidth)
            # print(self.winSize)

        # delete zero rows from line parameters
        lParams = lParams[~np.all(lParams == 0, axis=1)]
        winLoc = winLoc[~np.all(winLoc == 0, axis=1)]
        winMean = winMean[~np.all(winMean == 0, axis=1)]

        return lParams, winLoc, winMean
    
    def applyROI(self, img):
        _img = img.copy() 
        # defining ROI windown on the image
        if self.roiProp["enable_roi"]:
            r_pts = [self.roiProp["p1"], self.roiProp["p2"], self.roiProp["p3"], self.roiProp["p4"]]
            l_pts = [self.roiProp["p5"], self.roiProp["p6"], self.roiProp["p7"], self.roiProp["p8"]]

            cv.fillPoly(_img, np.array([r_pts]), (0, 0, 0))
            cv.fillPoly(_img, np.array([l_pts]), (0, 0, 0))
        return _img
    
    def getImageData(self):
        """function to extract the greenidx and the contour center points

        Returns:
            _type_: _description_
        """
        # change datatype to enable negative values for self.greenIDX calculation
        rgbImg = self.img.astype('int32')
        self.imgHeight, self.imgWidth, self.imgCh = self.img.shape

        rgbImg = self.applyROI(rgbImg)

        # cv.imshow("RGB image",imgInt32.astype('uint8'))
        # self.handleKey()
        #  get green index and binary mask (Otsu is used for extracting the green)
        self.mask, self.greenIDX = self.getExgMask(rgbImg)
        # find contours
        self.plantObjects = self.getClosedRegions(self.mask)

        # get center of contours
        contCenterPTS = self.getCCenter(self.plantObjects)
 
        x = contCenterPTS[:,1]
        y = contCenterPTS[:,0]
        contCenterPTS = np.array([x,y])

        return contCenterPTS, x, y

    def getClosedRegions(self, binrayMask):
        # find contours
        contours = cv.findContours(binrayMask, cv.RETR_TREE, cv.CHAIN_APPROX_NONE)[1]

        # cv.imshow("crop rows mask",self.mask)
        # self.handleKey()

        # filter contours based on size
        # self.filtered_contours = contours
        filtered_contours = list()
        for i in range(len(contours)):
            if cv.contourArea(contours[i]) > self.min_contour_area:
                
                if self.bushy : 
                    cn_x, cn_y, cnt_w, cn_h = cv.boundingRect(contours[i])

                    # split to N contours h/max_coutour_height
                    sub_contours = self.splitContours(contours[i], cn_x, cn_y, cnt_w, cn_h)
                    for j in sub_contours:
                        if j != []:
                            filtered_contours.append(j)
                else:
                    if contours[i] != []:
                        filtered_contours.append(contours[i]) 
            # else:
            #     filtered_contours.append(contours[i]) 
        return filtered_contours
    
    def splitContours(self, contour, x, y, w, h):
        """splits larg contours in smaller regions 

        Args:
            contour (_type_): _description_
            x (_type_): _description_
            y (_type_): _description_
            w (_type_): _description_
            h (_type_): _description_

        Returns:
            _type_: sub polygons (seperated countours)
        """
        sub_polygon_num = h // self.max_coutour_height
        sub_polys = list()
        subContour = list()
        vtx_idx = list()
        # contour = [sorted(contour.squeeze().tolist(), key=operator.itemgetter(0), reverse=True)]
        contour = [contour.squeeze().tolist()]
        for subPoly in range(1, sub_polygon_num + 1):
            for vtx in range(len(contour[0])): 
                if  (subPoly - 1 * self.max_coutour_height) -1 <=  contour[0][vtx][1] and \
                    (subPoly * self.max_coutour_height) -1 >= contour[0][vtx][1] and \
                    vtx not in vtx_idx:
                    subContour.append([contour[0][vtx]])
                    vtx_idx.append(vtx)

            sub_polys.append(np.array(subContour))
            subContour = list()

        return sub_polys

    def sortContours(self, cnts, method="left-to-right"):
        """initialize the reverse flag and sort index

        Args:
            cnts (_type_): _description_
            method (str, optional): _description_. Defaults to "left-to-right".

        Returns:
            _type_: sorted countours, bboxes
        """
        reverse = False
        i = 0
        # handle if we need to sort in reverse
        if method == "right-to-left" or method == "bottom-to-top":
            reverse = True
        # handle if we are sorting against the y-coordinate rather than
        # the x-coordinate of the bounding box
        if method == "top-to-bottom" or method == "bottom-to-top":
            i = 1
        # construct the list of bounding boxes and sort them from top to
        # bottom
        boundingBoxes = [cv.boundingRect(c) for c in cnts]
        (cnts, boundingBoxes) = zip(*sorted(zip(cnts, boundingBoxes),
            key=lambda b:b[1][i], reverse=reverse))
        # return the list of sorted contours and bounding boxes
        return cnts, boundingBoxes

    def getContoursInWindow(self, contourCenters, box):
        """iflters out countours inside a box

        Args:
            contourCenters (_type_): _description_
            box (_type_): _description_

        Returns:
            _type_: contour centers
        """
        points = []
        for cnt in range(len(contourCenters[1])):
            x, y = contourCenters[0][cnt], contourCenters[1][cnt]
            if self.isInBox(list(box), [x, y]):
                points.append([x, y])
        return points

    def isInBox(self, box, p):
        """checks if point is inside the box

        Args:
            box (_type_): box
            p (_type_): point

        Returns:
            _type_: True or False
        """
        bl = box[0] 
        tr = box[2]
        if (p[0] > bl[0] and p[0] < tr[0] and p[1] > bl[1] and p[1] < tr[1]) :
            return True
        else :
            return False
     
    def cameraToImage(self, P):
        """function to transform the feature point from camera to image frame

        Args:
            P (_type_): point in camera Fr

        Returns:
            _type_: point in image Fr
        """
        P[0] = P[0]- self.imgWidth/2
        P[1] = P[1]- self.imgHeight/2
        return P  
     
    def computeTheta(self, lineStart, lineEnd):
        """function to compute theta

        Args:
            lineStart (_type_): start point of line
            lineEnd (_type_): end point of line

        Returns:
            _type_: angle of line
        """
        return -(np.arctan2(abs(lineStart[1]-lineEnd[1]), lineStart[0]-lineEnd[0]))

    def lineIntersectWin(self, m,b):
        """function to compute the bottom and top intersect between the line and the window

        Args:
            m (_type_): slope
            b (_type_): bias

        Returns:
            _type_: to and bottom intersection with a box
        """
        # line calculations
        b_i = m*self.offsB+b
        t_i = m*(self.imgHeight- self.offsT)+b 
        return t_i, b_i    
      
    def lineIntersectIMG(self,m,b):
        """function to compute the bottom and top intersect between the line and the image 

        Args:
            m (_type_): slope
            b (_type_): bias

        Returns:
            _type_: top and bottom intersection points on image boarders
        """
        # line calculations
        b_i = b
        t_i = m*self.imgHeight+b   
        return t_i, b_i  
       
    def lineIntersectY(self,m,b,y):
        """ function to evaluate the estimated line

        Args:
            m (_type_): slope
            b (_type_): bias
            y (_type_): Y loc

        Returns:
            _type_: X loc
        """
        # line calculations
        x = m*y+b  
        return x  
    
    def lineIntersectSides(self,m,b):
        """_summary_

        Args:
            m (_type_): slope
            b (_type_): bias

        Returns:
            _type_: left and right interceptions
        """
        l_i = -b/m
        r_i = (self.imgWidth-b)/m
        return l_i, r_i
    
    def getCCenter(self, contours):
        """function to compute the center points of the contours

        Args:
            contours (_type_): contours from image

        Returns:
            _type_: contours centers
        """
        # get center of contours
        contCenterPTS = np.zeros((len(contours),2))
        for i in range(0, len(contours)):
            # get contour
            c_curr = contours[i];
            
            # get moments
            M = cv.moments(c_curr)
            
            # compute center of mass
            if(M['m00'] != 0):
               cx = int(M['m10']/M['m00'])
               cy = int(M['m01']/M['m00'])
               contCenterPTS[i,:] = [cy,cx]
                # draw point on countur centers
               self.processedIMG[cy-3:cy+3,cx-3:cx+3] = [255, 0, 255]

        contCenterPTS = contCenterPTS[~np.all(contCenterPTS == 0, axis=1)]
        return contCenterPTS
    
    def getLineRphi(self, xyCords):
        """sets r , phi line 

        Args:
            xyCords (_type_): x, y coordinates of point

        Returns:
            _type_: r, phi of line
        """
        x_coords, y_coords = zip(*xyCords)
        coefficients = np.polyfit(x_coords, y_coords, 1)
        return coefficients[0], coefficients[1]

    def drawGraphics(self):
        """function to draw the lines and the windows onto the image (self.processedIMG)
        """
        # main line
        cv.line(self.processedIMG, (int(self.mainLine_up[0]),int(self.mainLine_up[1])), (int(self.mainLine_down[0]),int(self.mainLine_down[1])), (255, 0, 0), thickness=3)
        # contoures
        cv.drawContours(self.processedIMG, self.plantObjects, -1, (10,50,150), 3)
        for i in range(0,len(self.allLineStart)):
            # helper lines
            cv.line(self.processedIMG, (int(self.allLineStart[i,0]),int(self.allLineStart[i,1])), (int(self.allLineEnd[i,0]),int(self.allLineEnd[i,1])), (0, 255, 0), thickness=1)
            
            if self.isInitialized:
                # 
                winBL = np.array([self.windowPoints[i,0], self.windowPoints[i,-2]], np.int32)
                winTL = np.array([self.windowPoints[i,1], self.windowPoints[i,-1]], np.int32)
                winTR = np.array([self.windowPoints[i,2], self.windowPoints[i,-1]], np.int32)
                winBR = np.array([self.windowPoints[i,3], self.windowPoints[i,-2]], np.int32)
                
                ptsPoly=np.c_[winBL, winBL, winBR, winTR, winTL].T
                cv.polylines(self.processedIMG, [ptsPoly], True, (0,0,0))
                        
    def handleKey(self, sleepTime=0):
        key = cv.waitKey(sleepTime)
        # Close program with keyboard 'q'
        if key == ord('q'):
            cv.destroyAllWindows()
            exit(1)
    # Function to compute the features of the line which has to be recognized
    def detectTrackingFeatures(self, mode):
        print("#[INF] detect Tracking Features")
        # Initiate SIFT detector
        sift = cv.xfeatures2d.SIFT_create()
        # find the keypoints and featureDescriptors in the green index image with SIFT
        greenIDX = self.getGreenIDX()
        # get sift key points
        keyPointsRaw, descriptorsRaw = sift.detectAndCompute(greenIDX,None)
        self.matchingKeypoints = []
        self.featureDescriptors = []
        # get the correct window depending on the current mode
        if mode == 3:
            windowLoc = self.windowLocations[0]
        else:
            windowLoc = self.windowLocations[-1]
        print("sample", self.windowLocations)
        # maintain the keypoints lying in the first window
        for kp, desc in itertools.izip(keyPointsRaw, descriptorsRaw):
            ptX = kp.pt[0]
            ptY = kp.pt[1]
            # if the computed keypoint is in the first window keep it
            if ptX > (windowLoc - 2 * self.turnWindowWidth) and ptX < (windowLoc + 2 * self.turnWindowWidth):
               if ptY > self.max_matching_dif_features  and ptY < (self.image_size[0] - self.min_matching_dif_features):
                    self.matchingKeypoints.append(kp)
                    self.featureDescriptors.append(desc)
                    # plot the first key points 
                    self.processedIMG[int(ptY)-3:int(ptY)+3,int(ptX)-3:int(ptX)+3] = [0, 0, 255]

        print(len(self.matchingKeypoints)," Key Points in the first window were detected")
 
    def matchTrackingFeatures(self, mode):
        """Function to compute the features in the second window
        """
        # Initiate SIFT detector
        sift = cv.xfeatures2d.SIFT_create()
        # find the keypoints and featureDescriptors in the green index image with SIFT
        greenIDX = self.getGreenIDX()
        # get sift key points
        keyPointsRaw, descriptorsRaw = sift.detectAndCompute(greenIDX,None)
        keyPtsCurr = []
        descriptorsCurr = []
        print("compare", self.windowLocations)
        # get the correct window depending on the current mode 
        if mode == 3:
            windowLoc = self.windowLocations[1]
        else:
            windowLoc = self.windowLocations[-2] 
        # maintain the keypoints lying in the second window
        for kp, desc in itertools.izip(keyPointsRaw, descriptorsRaw):
            ptX = kp.pt[0]
            ptY = kp.pt[1]
            # if the computed keypoint is in the second window keep it
            if ptX > (windowLoc - self.turnWindowWidth) and ptX < (windowLoc + self.turnWindowWidth):
               if ptY > self.max_matching_dif_features  and ptY < (self.imgWidth - self.min_matching_dif_features):
                    keyPtsCurr.append(kp)
                    descriptorsCurr.append(desc)   
                    # plot the key Points in the current image
                    self.processedIMG[int(ptY)-3:int(ptY)+3,int(ptX)-3:int(ptX)+3] = [255, 0, 0]
    
        # Check if there's a suitable number of key points 
        good = []
        if len(keyPtsCurr) > self.matching_keypoints_th:
            # compute the matches between the key points
            FLANN_INDEX_KDTREE = 1
            index_params = dict(algorithm = FLANN_INDEX_KDTREE, trees = 5)
            search_params = dict(checks = 50)
            flann = cv.FlannBasedMatcher(index_params, search_params)
            matches = flann.knnMatch(np.asarray(self.featureDescriptors,np.float32),np.asarray(descriptorsCurr,np.float32),k=2)
                
            # search for good matches (Lowes Ratio Test) 
            for m,n in matches:
                if m.distance < 0.8*n.distance:
                    good.append(m)
            
        # if not that indicates that the window is between two lines
        else:
            print('Not enough key points for matching for the ',self.nrNoPlantsSeen, ' time')
            self.nrNoPlantsSeen += 1
            print('# no plants seen:',self.nrNoPlantsSeen)
            if self.nrNoPlantsSeen > self.smoothSize:
                self.noPlantsSeen = True
        # cv bridge
        self.bridge = CvBridge()
        # publish processed image
        rosIMG = self.bridge.cv2_to_imgmsg(self.processedIMG, encoding='rgb8')
        self.numVec[self.count] = len(good)
        self.count += 1
        if self.count > self.smoothSize:
            self.meanVec[self.count] = sum(self.numVec[self.count-(self.smoothSize+1):self.count])/self.smoothSize

        # if the smoothed mean is descending the first row has passed the second window
        if self.meanVec[self.count]<self.meanVec[self.count-1] and self.meanVec[self.count] > self.matching_keypoints_th and self.noPlantsSeen:
            self.cnt += 1
            if self.cnt >= self.matching_keypoints_th:
                self.newDetectedRows += 1
                # if enough rows are passed
                if self.newDetectedRows == self.linesToPass:
                    self.cnt = 0
                    print("All rows passed")
                    return True, rosIMG
                else: 
                    print(self.newDetectedRows, " row(s) passed")
                    self.cnt = 0
                    # compute the new features in the new first window
                    self.computeTurnFeatures()
                    return False, rosIMG
            else:
                print("No row passed, continuing")
                return False, rosIMG
        else:
            print("No row passed, continuing")
            return False, rosIMG       
    # Function to compute the green index of the image
    def getExgMask(self, img):
        _img = img.copy()

        _img = _img.astype('int32')
        # Vegetation Mask
        r = _img[:,:,0]
        g = _img[:,:,1]
        b = _img[:,:,2]

        # calculate Excess Green Index and filter out negative values
        greenIDX = 2*g - r - b
        greenIDX[greenIDX<0] = 0
        greenIDX = greenIDX.astype('uint8')

        # Otsu's thresholding after gaussian smoothing
        blur = cv.GaussianBlur(greenIDX,(5,5),0)
        threshold, threshIMG = cv.threshold(blur,0,255,cv.THRESH_BINARY+cv.THRESH_OTSU)
        
        # dilation
        kernel = np.ones((10,10), np.uint8)
        binaryMask = cv.dilate(threshIMG, kernel, iterations=1)
        
        # Erotion
        # er_kernel = np.ones((10,10),dtype=np.uint8) # this must be tuned 
        # binaryMask= cv.erode(binaryMask, er_kernel)

        return binaryMask ,greenIDX            
            

