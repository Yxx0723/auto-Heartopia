"""Fish-line evidence, using the observed HSV bands and scaled geometry."""
import cv2
import numpy as np


class FishingLine:
    def __init__(self):
        self.previous = None

    def read(self, frame):
        counts = dict(white=0, yellow=0, orange=0, red=0)
        result = {'dom':'unknown', **counts}
        if frame is None or not frame.size:
            return result
        h,w = frame.shape[:2]
        sx,sy = w/1382, h/807
        x0,x1 = int(440*sx),int(720*sx)
        y0,y1 = int(125*sy),int(470*sy)
        if self.previous is not None:
            center = self.previous
            x0,x1 = max(x0,int(center-90*sx)),min(x1,int(center+90*sx))
        crop = frame[y0:y1,x0:x1]
        if not crop.size:
            self.previous = None
            return result
        hue,sat,value = cv2.split(cv2.cvtColor(crop,cv2.COLOR_BGR2HSV))
        bright = value > 180
        masks = {
            'white':bright & (sat<55),
            'yellow':bright & (sat>=55) & (hue>=15) & (hue<=40),
            'orange':bright & (sat>=80) & (hue>=8) & (hue<15),
            'red':bright & (sat>=100) & ((hue<8) | (hue>170)),
        }
        union = np.logical_or.reduce(list(masks.values())).astype(np.uint8)
        joined = cv2.morphologyEx(union, cv2.MORPH_CLOSE,
                                 np.ones((max(2,round(7*sy)),1),np.uint8))
        n,labels,stats,centers = cv2.connectedComponentsWithStats(joined,8)
        plausible = []
        for i in range(1,n):
            _,_,width,height,area = stats[i]
            if height >= max(8,30*sy) and height >= 2.2*width and width <= 48*sx and area:
                plausible.append(i)
        if not plausible:
            self.previous = None
            return result
        best = max(plausible,key=lambda i: (stats[i,cv2.CC_STAT_HEIGHT]
                   - .5*abs(x0+centers[i][0]-(self.previous or w*.41))))
        self.previous = x0+centers[best][0]
        region = labels==best
        counts = {name:int(np.count_nonzero(mask & region)) for name,mask in masks.items()}
        minimum = max(3,round(8*sx*sy))
        dominant = next((name for name in ('red','orange','yellow','white') if counts[name]>=minimum),'unknown')
        return {'dom':dominant, **counts, 'line_x':round(self.previous)}
