from voxel_globe.common_tasks import shared_task, VipTask

import os
from vsi.iglob import glob
import voxel_globe.meta.models
from os import environ as env
from os.path import join as path_join
import urllib

from django.contrib.gis import geos

import json;

from voxel_globe.tools.subprocessbg import Popen

from celery.utils.log import get_task_logger
logger = get_task_logger(__name__);

@shared_task(base=VipTask, bind=True)
def ingest_data(self, uploadSession_id, imageDir):
  ''' task for the ingest route, to ingest the data an upload sessions points 
      to '''
  import voxel_globe.ingest.models as IngestModels
  import numpy
  from voxel_globe.tools.camera import save_krt
  from PIL import Image

  uploadSession = IngestModels.UploadSession.objects.get(id=uploadSession_id);

  metadataFilenames = glob(os.path.join(imageDir, '*', '*.pos'), False);
  metadataBasenames = map(lambda x:os.path.split(x)[-1].lower(), metadataFilenames)

  try:
    timestamp = os.path.split(metadataFilenames[0])[1].split('-')[0];
    date = timestamp[0:4]+'-'+timestamp[4:6]+'-'+timestamp[6:8]
    timeOfDay = timestamp[8:10]+':'+timestamp[10:12]+':'+timestamp[12:14]
  except:
    metadataFilenames = [];
    date = 'NYA'
    timeOfDay = 'NYA'

  imageCollection = voxel_globe.meta.models.ImageCollection.create(
      name="Angelfire Upload %s %s %s (%s)" % (uploadSession.name, date, 
                                               timeOfDay, uploadSession_id), 
                                               service_id = self.request.id);
  imageCollection.save();

  llhs_xyz = []

  for d in glob(os.path.join(imageDir, '*'+os.path.sep), False):
    files = glob(os.path.join(d, '*.jpg'), False);
    files.sort()
    for index,f in enumerate(files):
      self.update_state(state='PROCESSING', 
                        meta={'stage':'File %s (%d of %d)' % (f, index, len(files))})
      logger.debug('Processing %s (%d of %d)', f, index, len(files))

      zoomifyName = f[:-4] + '_zoomify'
      pid = Popen(['vips', 'dzsave', f, zoomifyName, '--layout', 'zoomify'])
      pid.wait();

      #convert the slashes to URL slashes 
      relFilePath = urllib.pathname2url(os.path.relpath(f, env['VIP_IMAGE_SERVER_ROOT']));
      basename = os.path.split(f)[-1]
      relZoomPath = urllib.pathname2url(os.path.relpath(zoomifyName, env['VIP_IMAGE_SERVER_ROOT']));

      image = Image.open(f)
      if image.bits == 8:
        pixel_format = 'b';
      if image.bits == 16:
        pixel_format = 's';
      if image.bits == 32:
        if image.mode == "I":
          pixel_format = 'i';
        elif image.mode == "F":
          pixel_format = 'f'

      img = voxel_globe.meta.models.Image.create(
                             name="Angelfire Upload %s (%s) Frame %s" % (uploadSession.name, uploadSession_id, basename), 
                             imageWidth=image.size[0], imageHeight=image.size[1], 
                             numberColorBands=image.layers, pixelFormat=pixel_format, fileFormat='zoom', 
                             imageUrl='%s://%s:%s/%s/%s/' % (env['VIP_IMAGE_SERVER_PROTOCOL'], 
                                                             env['VIP_IMAGE_SERVER_HOST'], 
                                                             env['VIP_IMAGE_SERVER_PORT'], 
                                                             env['VIP_IMAGE_SERVER_URL_PATH'], 
                                                             relZoomPath),
                             originalImageUrl='%s://%s:%s/%s/%s' % (env['VIP_IMAGE_SERVER_PROTOCOL'], 
                                                                    env['VIP_IMAGE_SERVER_HOST'], 
                                                                    env['VIP_IMAGE_SERVER_PORT'], 
                                                                    env['VIP_IMAGE_SERVER_URL_PATH'], 
                                                                    relFilePath),
                             service_id = self.request.id);
      img.save();
     
      imageCollection.images.add(img);

      metadata_filename_desired = (os.path.splitext(os.path.split(f)[-1])[0][0:-6]+'00-VIS.pos').lower()
      try:
        metadata_index = metadataBasenames.index(metadata_filename_desired)
        metadata_filename = metadataFilenames[metadata_index]
        with open(metadata_filename, 'r') as fid:
          metadata = fid.readline().split(',')

        llh_xyz = [float(metadata[5]), float(metadata[4]), float(metadata[6])*0.3048]
        llhs_xyz.append(llh_xyz)
        k = numpy.eye(3);
        k[0,2] = img.imageWidth/2;
        k[1,2] = img.imageHeight/2;      
        r = numpy.eye(3);
        t = [0, 0, 0];
        origin = llh_xyz;
        save_krt(self.request.id, img, k, r, t, origin);#, srid=7428);
      except Exception as e:
        pass

  '''
  self.update_state(state='Processing', meta={'stage':'metadata'})      
  metadata = loadAdjTaggedMetadata(metadataFilename);
  for meta in metadata:
    try:
      img = imageCollection.images.get(name__icontains='Frame %s'%meta.filename)
      k = numpy.eye(3);
      k[0,2] = img.imageWidth/2;
      k[1,2] = img.imageHeight/2;      
      r = numpy.eye(3);
      t = [0, 0, 0];
      origin = meta.llh_xyz;
      save_krt(self.request.id, img, k, r, t, origin, srid=7428);
    except Exception as e:
      logger.warning('%s', e)
      logger.error('Could not match metadata entry for %s' % meta.filename)
  '''
  averageGps = numpy.mean(numpy.array(llhs_xyz), 0);
  
  voxel_globe.meta.models.Scene.create(name="Angelfire origin %s (%s)" % (uploadSession.name, uploadSession_id), 
                                       service_id = self.request.id,
                                       origin='POINT(%0.12f %0.12f %0.12f)' % \
                                       (averageGps[0], averageGps[1], averageGps[2])).save()
  uploadSession.delete()

ingest_data.dbname="angelfire"
ingest_data.description = "Angelfire data collect"

@shared_task(base=VipTask, bind=True)
def add_sample_images(self, imageDir, *args, **kwargs):
  ''' Demo ware only really '''
  images = glob(path_join(imageDir, '2010*', ''));
  images.sort();
  imageCollections = {};
  for image in images:
    cam = int(image[-7:-5])
    date = image[-31:-17];
    other = image[-16:-11]
    frameNum = image[-11:-8]
    image = os.path.basename(os.path.dirname(image));
    if voxel_globe.meta.models.Image.objects.filter(name="Purdue Data Date:%s Sequence:%s Camera:%d Frame:%s" % (date, other, cam, frameNum)):
      raise Exception('Already exists');

    img = voxel_globe.meta.models.Image.create(name="Purdue Data Date:%s Sequence:%s Camera:%d Frame:%s" % (date, other, cam, frameNum), imageWidth=3248, imageHeight=4872, 
                             numberColorBands=1, pixelFormat='b', fileFormat='zoom', 
                             imageUrl='%s://%s:%s/%s/%s/' % (env['VIP_IMAGE_SERVER_PROTOCOL'], 
                                                             env['VIP_IMAGE_SERVER_HOST'], 
                                                             env['VIP_IMAGE_SERVER_PORT'], 
                                                             env['VIP_IMAGE_SERVER_URL_PATH'], 
                                                             image),
                             originalImageUrl='%s://%s:%s/%s/%s.jpg' % (env['VIP_IMAGE_SERVER_PROTOCOL'], 
                                                                        env['VIP_IMAGE_SERVER_HOST'], 
                                                                        env['VIP_IMAGE_SERVER_PORT'], 
                                                                        env['VIP_IMAGE_SERVER_URL_PATH'], 
                                                                        image),
                             service_id = self.request.id);
    img.save();
    
    imageCollections[cam] = imageCollections.pop(cam, ())+(img.id,);
    
  for cam in range(6):
    ic = voxel_globe.meta.models.ImageCollection.create(name="Purdue Dataset Camera %d" % cam, service_id = self.request.id);
    ic.save();
    ic.images.add(*imageCollections[cam]);
  add_sample_cameras(self, path_join(env['VIP_PROJECT_ROOT'], 'images', 'purdue_cameras_1.txt')) #history = 1
  add_sample_cameras(self, path_join(env['VIP_PROJECT_ROOT'], 'images', 'purdue_cameras_2.txt')) #history = 2
  add_sample_cameras(self, path_join(env['VIP_PROJECT_ROOT'], 'images', 'purdue_cameras_3.txt')) #history = 3
  add_sample_cameras(self, path_join(env['VIP_PROJECT_ROOT'], 'images', 'purdue_cameras_4.txt')) #history = 4

@shared_task(base=VipTask, bind=True)
def add_sample_cameras2(self, filename, image_collection_id, srid=4326):
  image_collection = voxel_globe.meta.models.ImageCollection.objects.get(id=image_collection_id)

  with open(filename, 'r') as fid:
    #create a history object for the entire file for the demo
    for line in fid:
      l = eval(line);

      pos_filename = l[0];
      base_filename = os.path.splitext(os.path.split(pos_filename)[-1])[0]
      
      llh = l[1];
      ts = l[2:-1];
      k_i = l[-1];
      
      grcs = voxel_globe.meta.models.GeoreferenceCoordinateSystem.create(name='%s 0' % base_filename,
                                                               xUnit='d', yUnit='d', zUnit='m',
                                                               location='SRID=%d;POINT(%0.12f %0.12f %0.12f)' % ((srid,)+tuple(llh)),
                                                               service_id = self.request.id)
      grcs.save();

      last_cs = grcs;
      for t in range(len(ts)):
# This logic just doesn't work with the cheap getKTL and history tricks currently implemented, NEEDS TO BE REDONE
# Basically reverse FK'd don't work well with history... YET
# My JSON trick SHOULD work.... Maybe
#        try:
#          cs = voxel_globe.meta.models.CartesianCoordinateSystem.objects.get(name='%s %d' % (base_filename, t+1), newerVersion=None)
#        except voxel_globe.meta.models.CartesianCoordinateSystem.DoesNotExist:
        cs = voxel_globe.meta.models.CartesianCoordinateSystem.create(name='%s %d' % (base_filename, t+1),
                                                    service_id = self.request.id,
                                                    xUnit='m', yUnit='m', zUnit='m');
        cs.save();

        rx = geos.Point(*ts[t][0][0:3]);
        ry = geos.Point(*ts[t][1][0:3]);
        rz = geos.Point(*ts[t][2][0:3]);
        translation = geos.Point(ts[t][0][3], ts[t][1][3], ts[t][2][3]);

        transform = voxel_globe.meta.models.CartesianTransform.create(name='%s %d_%d' % (base_filename, t+1, t),
                                       service_id = self.request.id,
                                       rodriguezX=rx,rodriguezY=ry,rodriguezZ=rz,
                                       translation=translation,
                                       coordinateSystem_from_id=last_cs.id,
                                       coordinateSystem_to_id=cs.id)
        transform.save()

        last_cs = cs;

      camera = voxel_globe.meta.models.Camera.create(name=base_filename,
                                         service_id = self.request.id,
                                         focalLengthU=k_i[0],
                                         focalLengthV=k_i[1],
                                         principalPointU=k_i[2],
                                         principalPointV=k_i[3],
                                         coordinateSystem=last_cs)
      camera.save();
        
        
      #No longer necessary with the Django inspired "Leave the FK alone" technique

      images = image_collection.images.filter(imageUrl__contains=base_filename);

      for img in images:
        img2 = img.history(None)
        #img.service_id = self.request.id;
        img2.camera_id = camera.id;
        #img.update();
        img2.save()

#This BELONGS in a tools.py file, but I decided I don't care about this code
def add_sample_cameras(self, filename, srid=4326):
  with open(filename, 'r') as fid:
    history = dict();
    #create a history object for the entire file for the demo
    for line in fid:
      l = eval(line);

      pos_filename = l[0];
      base_filename = os.path.splitext(os.path.split(pos_filename)[-1])[0]
      
      llh = l[1];
      ts = l[2:-1];
      k_i = l[-1];
      
      try:
        grcs = voxel_globe.meta.models.GeoreferenceCoordinateSystem.objects.get(name='%s 0' % base_filename, newerVersion=None);
        grcs.service_id = self.request.id;
        grcs.update(location = 'SRID=%d;POINT(%0.12f %0.12f %0.12f)' % ((srid,)+tuple(llh)));
      except voxel_globe.meta.models.GeoreferenceCoordinateSystem.DoesNotExist:
        grcs = voxel_globe.meta.models.GeoreferenceCoordinateSystem.create(name='%s 0' % base_filename,
                                                               xUnit='d', yUnit='d', zUnit='m',
                                                               location='SRID=%d;POINT(%0.12f %0.12f %0.12f)' % ((srid,)+tuple(llh)),
                                                               service_id = self.request.id)
        grcs.save();
        
      history[grcs.objectId] = grcs.id;

      last_cs = grcs;
      for t in range(len(ts)):
# This logic just doesn't work with the cheap getKTL and history tricks currently implemented, NEEDS TO BE REDONE
# Basically reverse FK'd don't work well with history... YET
# My JSON trick SHOULD work.... Maybe
#        try:
#          cs = voxel_globe.meta.models.CartesianCoordinateSystem.objects.get(name='%s %d' % (base_filename, t+1), newerVersion=None)
#        except voxel_globe.meta.models.CartesianCoordinateSystem.DoesNotExist:
        try:
          cs = voxel_globe.meta.models.CartesianCoordinateSystem.objects.get(name='%s %d' % (base_filename, t+1), newerVersion=None)
          cs.service_id = self.request.id;
          cs.update();
        except:
          cs = voxel_globe.meta.models.CartesianCoordinateSystem.create(name='%s %d' % (base_filename, t+1),
                                                    service_id = self.request.id,
                                                    xUnit='m', yUnit='m', zUnit='m');
          cs.save();
          
        history[cs.objectId] = cs.id;

        rx = geos.Point(*ts[t][0][0:3]);
        ry = geos.Point(*ts[t][1][0:3]);
        rz = geos.Point(*ts[t][2][0:3]);
        translation = geos.Point(ts[t][0][3], ts[t][1][3], ts[t][2][3]);

        try:
          transform = voxel_globe.meta.models.CartesianTransform.objects.get(name='%s %d_%d' % (base_filename, t+1, t), newerVersion=None)
          transform.service_id = self.request.id;
          transform.coordinateSystem_from_id=last_cs.id;
          transform.coordinateSystem_to_id=cs.id;
          transform.update(rodriguezX=rx,rodriguezY=ry,rodriguezZ=rz,
                           translation=translation);
        except voxel_globe.meta.models.CartesianTransform.DoesNotExist:
          transform = voxel_globe.meta.models.CartesianTransform.create(name='%s %d_%d' % (base_filename, t+1, t),
                                       service_id = self.request.id,
                                       rodriguezX=rx,rodriguezY=ry,rodriguezZ=rz,
                                       translation=translation,
                                       coordinateSystem_from_id=last_cs.id,
                                       coordinateSystem_to_id=cs.id)
          transform.save()
          
        history[transform.objectId] = transform.id;
        last_cs = cs;

      try:
        camera = voxel_globe.meta.models.Camera.objects.get(name=base_filename, newerVersion=None);
        camera.service_id = self.request.id;
        camera.update(focalLengthU=k_i[0], focalLengthV=k_i[1],
                      principalPointU=k_i[2], principalPointV=k_i[3],
                      coordinateSystem=last_cs)
      except voxel_globe.meta.models.Camera.DoesNotExist:
        camera = voxel_globe.meta.models.Camera.create(name=base_filename,
                                         service_id = self.request.id,
                                         focalLengthU=k_i[0],
                                         focalLengthV=k_i[1],
                                         principalPointU=k_i[2],
                                         principalPointV=k_i[3],
                                         coordinateSystem=last_cs)
        camera.save();
        
      history[camera.objectId] = camera.id;
        
        
      #No longer necessary with the Django inspired "Leave the FK alone" technique

      images = voxel_globe.meta.models.Image.objects.filter(imageUrl__contains=base_filename);

      for img in images:
        #img.service_id = self.request.id;
        img.camera_id = camera.id;
        #img.update();
        img.save()
        
        history[img.objectId] = img.id;
        
  history = voxel_globe.meta.models.History(name=filename, history=json.dumps(history))
  history.save();

  
@shared_task(base=VipTask, bind=True)
def add_control_point(self, controlpoint_filename):
  from math import copysign
  with open(controlpoint_filename, 'r') as fid:
    lines = fid.readlines();
  lines = map(lambda x: x.split(','), lines)
  for line in lines:
    name = line[0];
    #Get just the name, the only text field
    fields = map(float, line[1:])
    #Float cast the rest
    
    latitude  = fields[0]
    latitude += copysign(fields[1]/60.0 + fields[2]/3600.0, latitude);
    longitude = fields[3]
    longitude += copysign(fields[4]/60.0 + fields[5]/3600.0, longitude);
    altitude  = fields[6]
    point = 'SRID=4326;POINT(%0.12f %0.12f %0.12f)' % (longitude, latitude, altitude);

    other = 'Utm %f %f %f Other %f %f %f' % tuple(fields[7:])
    tp = voxel_globe.meta.models.ControlPoint.create(name=name,
                                         description=other,
                                         point=point,
                                         apparentPoint=point)
    tp.service_id = self.request.id;
    tp.save();

@shared_task(base=VipTask, bind=True)
def add_sample_tie_point(self, site_filename, lvcs_selected_filename, camera, frames):
  ''' Demo ware only, really '''
  from voxel_globe.tools.xml_dict import load_xml
  control_point_names = [];
  with open(lvcs_selected_filename, 'r') as fid:
    for line in fid:
      control_point_names.append(line.split(' ')[0].strip())
  tie_point_data = load_xml(site_filename);
  
  for control_point_index in range(len(control_point_names)):
    cpn = control_point_names[control_point_index];
    cp = voxel_globe.meta.models.ControlPoint.objects.get(name=cpn);
    for frame in frames:
      frame_num = int(frame);
      tp = tie_point_data['Correspondences']['Correspondence'][control_point_index]['CE'].find_at(fr__is='%d'%frame);
      if tp:
        tp = tp[0].at;
        if camera is not None:
          name = '%s Camera:%d Frame:%03d' % (cpn, camera, frame_num)
          img_name = 'Camera:%d Frame:%03d' % (camera, frame_num)
        else:
          name = '%s Frame:%03d' % (cpn, frame_num+1)
          img_name = 'Mission 2 Frame:%04d' % (frame_num+1)

        point = 'POINT(%s %s)' % (tp['u'], tp['v']);
        image = voxel_globe.meta.models.Image.objects.get(name__contains=img_name)

        tp = voxel_globe.meta.models.TiePoint.create(name=name, point=point, image=image, geoPoint=cp)
        tp.service_id = self.request.id;
        tp.save();

@shared_task(base=VipTask, bind=True)
def update_sample_tie_point(self, tiepoint_filename):
  ''' Demo ware only, really '''
  with open(tiepoint_filename, 'r') as fid:
    lines = fid.readlines();
  lines = map(lambda x: x.split('\x00'), lines)
  
  for tp in lines:
    img = voxel_globe.meta.models.Image.objects.get(name=tp[0], newerVersion=None)
    cp = voxel_globe.meta.models.ControlPoint.objects.get(name=tp[1], newerVersion=None)
    TP = voxel_globe.meta.models.TiePoint.objects.get(geoPoint=cp, image=img, newerVersion=None)
    
    TP.service_id = self.request.id;
    TP.point = 'POINT(%s %s)' % (tp[2], tp[3].strip())
    TP.update()
