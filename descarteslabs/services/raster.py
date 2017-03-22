import base64
import json
from descarteslabs.addons import FeatureArray
from descarteslabs.addons import numpy as np
from .service import Service
from .places import Places


class Raster(Service):
    """Raster"""
    TIMEOUT = 300

    def __init__(
            self,
            url='https://platform-services.descarteslabs.com/raster',
            token=None
    ):
        """The parent Service class implements authentication and exponential
        backoff/retry. Override the url parameter to use a different instance
        of the backing service.
        """
        Service.__init__(self, url, token)

    def get_bands_by_key(self, key):
        r = self.session.get('%s/bands/key/%s' % (self.url, key), timeout=self.TIMEOUT)

        if r.status_code != 200:
            raise RuntimeError("%s: %s" % (r.status_code, r.text))

        return r.json()

    def get_bands_by_constellation(self, const):
        r = self.session.get('%s/bands/constellation/%s' % (self.url, const), timeout=self.TIMEOUT)

        if r.status_code != 200:
            raise RuntimeError("%s: %s" % (r.status_code, r.text))

        return r.json()

    def dlkeys_from_shape(self, resolution, tilesize, pad, shape):
        params = {
            'resolution': resolution,
            'tilesize': tilesize,
            'pad': pad,
            'shape': shape,
        }

        r = self.session.post('%s/dlkeys/from_shape' % (self.url), json=params, timeout=self.TIMEOUT)

        if r.status_code != 200:
            raise RuntimeError("%s: %s" % (r.status_code, r.text))

        return r.json()

    def dlkey_from_latlon(self, lat, lon, resolution, tilesize, pad):
        params = {
            'resolution': resolution,
            'tilesize': tilesize,
            'pad': pad,
        }

        r = self.session.get('%s/dlkeys/from_latlon/%f/%f' % (self.url, lat, lon),
                             params=params, timeout=self.TIMEOUT)

        if r.status_code != 200:
            raise RuntimeError("%s: %s" % (r.status_code, r.text))

        return r.json()

    def dlkey(self, key):

        r = self.session.get('%s/dlkeys/%s' % (self.url, key), timeout=self.TIMEOUT)

        if r.status_code != 200:
            raise RuntimeError("%s: %s" % (r.status_code, r.text))

        return r.json()

    def raster(
            self,
            inputs,
            bands=None,
            scales=None,
            data_type=None,
            output_format='GTiff',
            srs=None,
            dimensions=None,
            resolution=None,
            bounds=None,
            bounds_srs=None,
            shape=None,
            location=None,
            align_pixels=False,
            resampler=None,
    ):
        """
        Yield a raster composed from one/many sources

        Given a list of filenames, generate a translated and merged mosaic as
        a new GDAL dataset, and yield it to the user.

        Parameters
        ----------
        inputs: list
            list of metadata keys
        bands: list, optional
            A list of bands (1-indexed) that correspond to the source raster
            bands to be used.
        scales: list, Optional
            A list of tuples specifying the scaling to be applied to each band
            (indexed by the destination bands). If None, no scaling we be
            applied. If scaling should only be applied to a subset of bands,
            pad the list with None entries where appropriate.  If an entry is
            of length 4, the destination scales will be included.  (0, 1, 10,
            100) would scale 0->10 and 1->100. Default: None
        output_format: str, optional
            Output format ("GTiff", "PNG", ...). Default: "GTiff"
        data_type: str, optional
            Output type ('Byte', 'UInt16', etc). Default: None (same as source
            type)
        srs: str, optional
            Output projection SRS. Can be any gdal.Warp compatible SRS
            definition.  Default: None (same as first source)
        resolution: float, optional
            Output resolution, in srs coordinate system. Default: None (native
            resolution).
        dimensions: tuple of integers, optional
            Desired image size of output. Incompatible with resolution.
        shape: str, optional
            A GeoJSON string used for a cutline. Default: None
        location: str, optional
            A named location to be used as a cutline, retrieved via Places.
            Incompatible with "shape". Default: None
        bounds: list, optional
            Output bounds as (minX, minY, maxX, maxY) in target SRS.
            Default None.
        bounds_srs: str, optional
            SRS in which outputBounds are expressed, in the case that they are
            not expressed in the output SRS.
        align_pixels: bool, optional
            Target aligned pixels with the coordinate system. Default: False
        resampler: str, optional
            Resampling algorithm to use in the Warp. Default: None
        """

        if location:
            places = Places()
            shape = places.shape(location, geom='low')
            shape = json.dumps(shape['geometry'])

        params = {
            'keys': inputs,
            'bands': bands,
            'scales': scales,
            'ot': data_type,
            'of': output_format,
            'srs': srs,
            'resolution': resolution,
            'shape': shape,
            'outputBounds': bounds,
            'outputBoundsSRS': bounds_srs,
            'outsize': dimensions,
            'targetAlignedPixels': align_pixels,
            'resampleAlg': resampler,
        }

        r = self.session.post('%s/raster' % (self.url), json=params, timeout=self.TIMEOUT)

        if r.status_code != 200:
            raise RuntimeError("%s: %s" % (r.status_code, r.text))

        json_resp = r.json()
        # Decode base64
        for k in json_resp['files'].keys():
            json_resp['files'][k] = base64.b64decode(json_resp['files'][k])

        return json_resp

    def ndarray(
            self,
            inputs,
            bands=None,
            scales=None,
            data_type=None,
            srs=None,
            resolution=None,
            dimensions=None,
            shape=None,
            location=None,
            bounds=None,
            bounds_srs=None,
            align_pixels=False,
            resampler=None,
            order='image',
    ):
        """
        Yield a raster composed from one/many sources

        Given a list of filenames, generate a translated and merged mosaic as
        a new GDAL dataset, and yield it to the user.

        Parameters
        ----------
        inputs: list
            list of metadata keys
        bands: list, optional
            A list of bands (1-indexed) that correspond to the source raster
            bands to be used.
        scales: list, Optional
            A list of tuples specifying the scaling to be applied to each band
            (indexed by the destination bands). If None, no scaling we be
            applied. If scaling should only be applied to a subset of bands,
            pad the list with None entries where appropriate.  If an entry is
            of length 4, the destination scales will be included.  (0, 1, 10,
            100) would scale 0->10 and 1->100. Default: None
        data_type: str, optional
            Output type ('Byte', 'UInt16', etc). Default: None (same as source
            type)
        srs: str, optional
            Output projection SRS. Can be any gdal.Warp compatible SRS
            definition.  Default: None (same as first source)
        resolution: float, optional
            Output resolution, in srs coordinate system. Default: None (native
            resolution).
        dimensions: list of integers, optional
            Desired image size of output. Incompatible with resolution.
        shape: str, optional
            A GeoJSON string used for a cutline. Default: None
        location: str, optional
            A named location to be used as a cutline, retrieved via Places.
            Incompatible with "shape". Default: None
        bounds: list, optional
            Output bounds as (minX, minY, maxX, maxY) in target SRS.
            Default None.
        bounds_srs: str, optional
            SRS in which outputBounds are expressed, in the case that they are
            not expressed in the output SRS.
        align_pixels: bool, optional
            Target aligned pixels with the coordinate system. Default: False
        resampler: str, optional
            Resampling algorithm to use in the Warp. Default: None
        order: str, optional
            Order of returned array.
            'image' (default) returns arrays in  (row, column, band).
            'gdal' returns arrays in  (band, row, column).
        """

        if location is not None:
            places = Places()
            shape = places.shape(location, geom='low')
            shape = json.dumps(shape['geometry'])

        params = {
            'keys': inputs,
            'bands': bands,
            'scales': scales,
            'ot': data_type,
            'srs': srs,
            'resolution': resolution,
            'shape': shape,
            'outputBounds': bounds,
            'outputBoundsSRS': bounds_srs,
            'outsize': dimensions,
            'targetAlignedPixels': align_pixels,
            'resampleAlg': resampler,
        }

        r = self.session.post('%s/featurearray' % (self.url), json=params, timeout=self.TIMEOUT)

        if r.status_code != 200:
            raise RuntimeError("%s: %s" % (r.status_code, r.text))

        fa = FeatureArray.deserialize(r.content, base64.b64decode)

        if order == 'image':
            return fa.transpose((1, 2, 0)).view(np.ndarray)
        elif order == 'gdal':
            return fa.view(np.ndarray)
