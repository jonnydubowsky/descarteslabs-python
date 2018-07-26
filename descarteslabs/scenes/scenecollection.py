# Copyright 2018 Descartes Labs.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Create a SceneCollection by searching:

.. ipython::

    In [1]: import descarteslabs as dl

    In [2]: import numpy as np

    In [3]: aoi_geometry = {'type': 'Polygon',
       ...:  'coordinates': (((-95.27841503861751, 42.76556057019057),
       ...:    (-93.15675252485482, 42.36289849433184),
       ...:    (-93.73350276458868, 40.73810018004927),
       ...:    (-95.79766011799035, 41.13809376845988),
       ...:    (-95.27841503861751, 42.76556057019057)),)}

    In [4]: scenes, ctx = dl.scenes.search(aoi_geometry, products=["landsat:LC08:PRE:TOAR"], limit=10)

    @doctest
    In [5]: scenes
    Out[5]:
    SceneCollection of 10 scenes
      * Dates: Apr 18, 2013 to Sep 09, 2013
      * Products: landsat:LC08:PRE:TOAR: 10

Use `SceneCollection.each` and `SceneCollection.filter` to subselect Scenes you want:

.. ipython::

    In [6]: # which month is each scene from?

    @doctest
    In [7]: scenes.each.properties.date.month.combine()
    Out[7]: [4, 5, 5, 6, 6, 7, 7, 8, 8, 9]

    In [8]: spring_scenes = scenes.filter(lambda s: s.properties.date.month <= 6)

    @doctest
    In [9]: spring_scenes
    Out[9]:
    SceneCollection of 5 scenes
      * Dates: Apr 18, 2013 to Jun 21, 2013
      * Products: landsat:LC08:PRE:TOAR: 5

Operate on related Scenes with `SceneCollection.groupby`:

.. ipython::

    @doctest
    In [14]: for month, month_scenes in spring_scenes.groupby("properties.date.month"):
       ....:    print("Month {}: {} scenes".format(month, len(month_scenes)))
       ....:
    Month 4: 1 scenes
    Month 5: 2 scenes
    Month 6: 2 scenes

Load data with `SceneCollection.stack` or `SceneCollection.mosaic`:

.. ipython::

    In [10]: ctx_lowres = ctx.assign(resolution=120)

    In [11]: stack = spring_scenes.stack("red green blue", ctx_lowres)

    In [12]: stack.shape
    Out[12]: (5, 3, 3690, 3724)
"""

from __future__ import division
import collections
import six
import logging
import json

from descarteslabs.client.addons import ThirdParty, concurrent, shapely, numpy as np

from descarteslabs.client.services.raster import Raster
from descarteslabs.client.exceptions import NotFoundError, BadRequestError

from .collection import Collection
from .scene import Scene

have_shapely = not isinstance(shapely, ThirdParty)


class SceneCollection(Collection):
    """
    Holds Scenes, with methods for loading their data.

    As a subclass of `Collection`, the `filter`, `map`, and `groupby`
    methods and `each` property simplify inspection and subselection of
    contianed Scenes.

    `stack` and `mosaic` rasterize all contained Scenes into an ndarray
    using the a GeoContext.
    """
    def __init__(self, iterable=None, raster_client=None):
        super(SceneCollection, self).__init__(iterable)
        self._raster_client = raster_client if raster_client is not None else Raster()

    def map(self, f):
        """
        Returns list of ``f`` applied to each item in self,
        or SceneCollection if ``f`` returns Scenes
        """
        res = super(SceneCollection, self).map(f)
        if all(isinstance(x, Scene) for x in res):
            return res
        else:
            return list(res)

    def stack(self,
              bands,
              ctx,
              flatten=None,
              mask_nodata=True,
              mask_alpha=True,
              bands_axis=1,
              raster_info=False,
              max_workers=None,
              ):
        """
        Load bands from all scenes and stack them into a 4D ndarray,
        optionally masking invalid data.

        Parameters
        ----------
        bands : str or Sequence[str]
            Band names to load. Can be a single string of band names
            separated by spaces (``"red green blue"``),
            or a sequence of band names (``["red", "green", "blue"]``).
            If the alpha band is requested, it must be last in the list
            to reduce rasterization errors.
        ctx : GeoContext
            A GeoContext to use when loading each Scene
        flatten : str, Sequence[str], callable, or Sequence[callable], default None
            "Flatten" groups of Scenes in the stack into a single layer by mosaicking
            each group (such as Scenes from the same day), then stacking the mosaics.

            ``flatten`` takes the same predicates as `Collection.groupby`, such as
            ``"properties.date"`` to mosaic Scenes acquired at the exact same timestamp,
            or ``["properties.date.year", "properties.date.month", "properties.date.day"]``
            to combine Scenes captured on the same day (but not necessarily the same time).

            This is especially useful when ``ctx`` straddles a scene boundary
            and contains one image captured right after another. Instead of having
            each as a separate layer in the stack, you might want them combined.

            Note that indicies in the returned ndarray will no longer correspond to
            indicies in this SceneCollection, since multiple Scenes may be combined into
            one layer in the stack. You can call ``groupby`` on this SceneCollection
            with the same parameters to iterate through groups of Scenes in equivalent
            order to the returned ndarray.

            Additionally, the order of scenes in the ndarray will change:
            they'll be sorted by the parameters to ``flatten``.
        mask_nodata : bool, default True
            Whether to mask out values in each band of each scene that equal
            that band's ``nodata`` sentinel value.
        mask_alpha : bool, default True
            Whether to mask pixels in all bands of each scene where
            the alpha band is 0.
        bands_axis : int, default 1
            Axis along which bands should be located.
            If 1, the array will have shape ``(scene, band, y, x)``, if -1,
            it will have shape ``(scene, y, x, band)``, etc.
            A bands_axis of 0 is currently unsupported.
        raster_info : bool, default False
            Whether to also return a list of dicts about the rasterization of
            each scene, including the coordinate system WKT
            and geotransform matrix.
            Generally only useful if you plan to upload data derived from this
            scene back to the Descartes catalog, or use it with GDAL.
        max_workers : int, default None
            Maximum number of threads to use to parallelize individual ndarray
            calls to each Scene.
            If None, it defaults to the number of processors on the machine,
            multiplied by 5.
            Note that unnecessary threads *won't* be created if ``max_workers``
            is greater than the number of Scenes in the SceneCollection.

        Returns
        -------
        arr : ndarray
            Returned array's shape is ``(scene, band, y, x)`` if bands_axis is 1,
            or ``(scene, y, x, band)`` if bands_axis is -1.
            If ``mask_nodata`` or ``mask_alpha`` is True, arr will be a masked array.
        raster_info : List[dict]
            If ``raster_info=True``, a list of raster information dicts for each scene
            is also returned

        Raises
        ------
        ValueError
            If requested bands are unavailable, or band names are not given
            or are invalid.
            If not all required parameters are specified in the GeoContext.
            If the SceneCollection is empty.
        NotFoundError
            If a Scene's id cannot be found in the Descartes Labs catalog
        BadRequestError
            If the Descartes Labs platform is given unrecognized parameters
        """
        if len(self) == 0:
            raise ValueError("This SceneCollection is empty")

        kwargs = dict(
            mask_nodata=mask_nodata,
            mask_alpha=mask_alpha,
            bands_axis=bands_axis,
            raster_info=raster_info,
        )

        if bands_axis == 0 or bands_axis == -4:
            raise NotImplementedError(
                "bands_axis of 0 is currently unsupported for `SceneCollection.stack`. "
                "If you require this shape, try ``np.moveaxis(my_stack, 1, 0)`` on the returned ndarray."
            )
        elif bands_axis > 0:
            kwargs['bands_axis'] = bands_axis - 1  # the bands axis for each component ndarray call in the stack

        if flatten is not None:
            if isinstance(flatten, six.string_types) or not hasattr(flatten, "__len__"):
                flatten = [flatten]
            scenes = [sc if len(sc) > 1 else sc[0] for group, sc in self.groupby(*flatten)]
        else:
            scenes = self

        full_stack = None
        mask = None
        if raster_info:
            raster_infos = [None] * len(scenes)

        bands = Scene._bands_to_list(bands)
        pop_alpha = False
        if (mask_nodata or mask_alpha) and "alpha" not in bands:
            pop_alpha = True
            bands.append("alpha")
        # Pre-check that all bands and alpha are available in all Scenes, and all have the same dtypes
        self._common_data_type(bands)
        if pop_alpha:
            bands.pop(-1)

        def threaded_ndarrays():
            def data_loader(scene_or_scenecollection, bands, ctx, **kwargs):
                ndarray_kwargs = dict(kwargs, raster_client=self._raster_client)
                if isinstance(scene_or_scenecollection, self.__class__):
                    return lambda: scene_or_scenecollection.mosaic(bands, ctx, **kwargs)
                else:
                    return lambda: scene_or_scenecollection.ndarray(bands, ctx, **ndarray_kwargs)

            try:
                futures = concurrent.futures
            except ImportError:
                logging.warning(
                    "Failed to import concurrent.futures. ndarray calls will be serial."
                )
                for i, scene_or_scenecollection in enumerate(scenes):
                    yield i, data_loader(scene_or_scenecollection, bands, ctx, **kwargs)()
            else:
                with futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_ndarrays = {}
                    for i, scene_or_scenecollection in enumerate(scenes):
                        future_ndarray = executor.submit(data_loader(scene_or_scenecollection, bands, ctx, **kwargs))
                        future_ndarrays[future_ndarray] = i
                    for future in futures.as_completed(future_ndarrays):
                        i = future_ndarrays[future]
                        result = future.result()
                        yield i, result

        for i, arr in threaded_ndarrays():
            if raster_info:
                arr, raster_meta = arr
                raster_infos[i] = raster_meta

            if full_stack is None:
                stack_shape = (len(scenes),) + arr.shape
                full_stack = np.empty(stack_shape, dtype=arr.dtype)
                if isinstance(arr, np.ma.MaskedArray):
                    mask = np.empty(stack_shape, dtype=bool)

            if isinstance(arr, np.ma.MaskedArray):
                full_stack[i] = arr.data
                mask[i] = arr.mask
            else:
                full_stack[i] = arr

        if mask is not None:
            full_stack = np.ma.MaskedArray(full_stack, mask, copy=False)
        if raster_info:
            return full_stack, raster_infos
        else:
            return full_stack

    def mosaic(self,
               bands,
               ctx,
               mask_nodata=True,
               mask_alpha=True,
               bands_axis=0,
               raster_info=False,
               ):
        """
        Load bands from all scenes, combining them into a single 3D ndarray
        and optionally masking invalid data.

        Where multiple scenes overlap, only data from the scene that comes last
        in the SceneCollection is used.

        Parameters
        ----------
        bands : str or Sequence[str]
            Band names to load. Can be a single string of band names
            separated by spaces (``"red green blue"``),
            or a sequence of band names (``["red", "green", "blue"]``).
            If the alpha band is requested, it must be last in the list
            to reduce rasterization errors.
        ctx : GeoContext
            A GeoContext to use when loading each Scene
        mask_nodata : bool, default True
            Whether to mask out values in each band that equal
            that band's ``nodata`` sentinel value.
        mask_alpha : bool, default True
            Whether to mask pixels in all bands where the alpha band of all scenes is 0.
        bands_axis : int, default 0
            Axis along which bands should be located in the returned array.
            If 0, the array will have shape ``(band, y, x)``,
            if -1, it will have shape ``(y, x, band)``.

            It's usually easier to work with bands as the outermost axis,
            but when working with large arrays, or with many arrays concatenated
            together, NumPy operations aggregating each xy point across bands
            can be slightly faster with bands as the innermost axis.
        raster_info : bool, default False
            Whether to also return a dict of information about the rasterization
            of the scenes, including the coordinate system WKT and geotransform matrix.
            Generally only useful if you plan to upload data derived
            from this scene back to the Descartes catalog, or use it with GDAL.

        Returns
        -------
        arr : ndarray
            Returned array's shape will be ``(band, y, x)`` if ``bands_axis``
            is 0, and ``(y, x, band)`` if ``bands_axis`` is -1.
            If ``mask_nodata`` or ``mask_alpha`` is True, arr will be a masked array.
        raster_info : dict
            If ``raster_info=True``, a raster information dict is also returned.

        Raises
        ------
        ValueError
            If requested bands are unavailable, or band names are not given
            or are invalid.
            If not all required parameters are specified in the GeoContext.
            If the SceneCollection is empty.
        NotFoundError
            If a Scene's ID cannot be found in the Descartes Labs catalog
        BadRequestError
            If the Descartes Labs platform is given unrecognized parameters
        """
        if len(self) == 0:
            raise ValueError("This SceneCollection is empty")

        if not (-3 < bands_axis < 3):
            raise ValueError("Invalid bands_axis; axis {} would not exist in a 3D array".format(bands_axis))

        bands = Scene._bands_to_list(bands)

        if mask_alpha:
            try:
                alpha_i = bands.index("alpha")
            except ValueError:
                bands.append("alpha")
                drop_alpha = True
            else:
                if alpha_i != len(bands) - 1:
                    raise ValueError("Alpha must be the last band in order to reduce rasterization errors")
                drop_alpha = False

        common_data_type = self._common_data_type(bands)

        raster_params = ctx.raster_params
        full_raster_args = dict(
            inputs=[scene.properties["id"] for scene in self],
            order="gdal",
            bands=bands,
            scales=None,
            data_type=common_data_type,
            **raster_params
        )

        try:
            arr, info = self._raster_client.ndarray(**full_raster_args)
        except NotFoundError as e:
            raise NotFoundError(
                "Some or all of these IDs don't exist in the Descartes catalog: {}".format(full_raster_args["inputs"])
            )
        except BadRequestError as e:
            msg = ("Error with request:\n"
                   "{err}\n"
                   "For reference, dl.Raster.ndarray was called with these arguments:\n"
                   "{args}")
            msg = msg.format(err=e, args=json.dumps(full_raster_args, indent=2))
            six.raise_from(BadRequestError(msg), None)

        if len(arr.shape) == 2:
            # if only 1 band requested, still return a 3d array
            arr = arr[np.newaxis]

        if mask_nodata or mask_alpha:
            if mask_alpha:
                alpha = arr[-1]
                if drop_alpha:
                    arr = arr[:-1]
                    bands.pop(-1)

            mask = np.zeros_like(arr, dtype=bool)

            if mask_nodata:
                # collect all possible nodata values per band,
                # in case different products have different nodata values for the same-named band
                # QUESTION: is this overkill?
                band_nodata_values = collections.defaultdict(set)
                for scene in self:
                    scene_bands = scene.properties["bands"]
                    for bandname in bands:
                        band_nodata_values[bandname].add(scene_bands[bandname].get('nodata'))

                for i, bandname in enumerate(bands):
                    for nodata in band_nodata_values[bandname]:
                        if nodata is not None:
                            mask[i] |= arr[i] == nodata

            if mask_alpha:
                mask |= alpha == 0

            arr = np.ma.MaskedArray(arr, mask, copy=False)

        if bands_axis != 0:
            arr = np.moveaxis(arr, 0, bands_axis)
        if raster_info:
            return arr, info
        else:
            return arr

    def __repr__(self):
        parts = ["SceneCollection of {} scene{}".format(len(self), "" if len(self) == 1 else "s")]
        try:
            first = min(self.each.properties.date)
            last = max(self.each.properties.date)
            dates = "  * Dates: {:%b %d, %Y} to {:%b %d, %Y}".format(first, last)
            parts.append(dates)
        except Exception:
            pass

        try:
            products = self.each.properties.product.combine(collections.Counter)
            if len(products) > 0:
                products = ", ".join("{}: {}".format(k, v) for k, v in six.iteritems(products))
                products = "  * Products: {}".format(products)
                parts.append(products)
        except Exception:
            pass

        return "\n".join(parts)

    def _common_data_type(self, bands):
        data_types = [scene._common_data_type_of_bands(bands) for scene in self]
        common_data_type = None
        for i, data_type in enumerate(data_types):
            if common_data_type is None:
                common_data_type = data_type
            else:
                if data_type != common_data_type:
                    raise ValueError(
                        "Bands must all have the same dtype in every Scene. "
                        "The requested bands in Scene {} have dtype '{}', but all prior Scenes had dtype '{}'"
                        .format(i, data_type, common_data_type)
                    )
        return common_data_type