"""
Module to verify Snapshot based Namespace level mirroring from Non default to default namespace.

Test case covered -
CEPH-83612860 -  Snapshot based Namespace level mirroring from Non default to default namespace

Pre-requisites :
1. Two ceph clusters version 8.1 or later with mon,mgr,osd
2. Deploy rbd-mirror daemon service on both clusters

Test Case Flow:
1. Create a pool  on both clusters:
2. Create namespaces ns1_p in pool1 on cluster1:
3. Enable non-default namespace mirroring on with "init-only" mirror mode on cluster-1
   # rbd mirror pool enable --pool pool1 init-only
   # rbd mirror pool enable pool1/ns1_p image --remote-namespace ' '
   Enable default namespace image mode mirroring on cluster-2
   # rbd mirror pool enable --pool pool1 image --remote-namespace ns1_p
4. Set up peering between the two clusters in two-way mode
5. Verify mirroring is configured successfully using below command on both clusters
6. Create an image in the namespace ns1_p on cluster1 and enable snapshot-based mirroring:
7. Verify image mirroring status for the namespace image
8. Add a snapshot schedule for the mirrored image in the namespace level:
9. Initiate I/O operations on the image using rbd bench or fio or file mount:
10. Wait till snapshot schedule interval set like above 1m
11. Verify that data is mirrored from the primary to the secondary cluster
12. Verify data consistency using md5sum checksum from primary and secondary
13. Similarly, create image in the default namespace in cluster2 and verify that the image
    got mirrored to cluster1 remote namespace
14. Add a snapshot schedule for the mirrored image in the namespace
15. Initiate I/O operations on the image using rbd bench, fio, or file mount
16. Wait till snapshot schedule interval set like above 1m.
17. Verify that data is mirrored from cluster-2 to cluster-1
18. Verify data consistency using md5sum checksum from primary and secondary
19.  Repeat the above test on EC pool
20. Cleanup the images, namespace, pools along with disk cleanup.
"""

import ast
import time

from ceph.rbd.initial_config import initial_mirror_config
from ceph.rbd.utils import check_data_integrity, getdict, random_string
from ceph.rbd.workflows.cleanup import cleanup
from ceph.rbd.workflows.krbd_io_handler import krbd_io_handler
from ceph.rbd.workflows.rbd_mirror import enable_image_mirroring, wait_for_status
from ceph.rbd.workflows.snap_scheduling import (
    add_snapshot_scheduling,
    verify_namespace_snapshot_schedule,
    verify_snapshot_schedule,
)
from utility.log import Log

log = Log(__name__)


def test_non_default_to_default_namespace_mirroring(
    pri_config, sec_config, pool_types, **kw
):
    log.info(
        "Starting CEPH-83612860 - Snapshot based Namespace "
        + "level mirroring from Non default to default namespace"
    )
    rbd_primary = pri_config.get("rbd")
    rbd_secondary = sec_config.get("rbd")
    client_primary = pri_config.get("client")
    client_secondary = sec_config.get("client")

    for pool_type in pool_types:
        rbd_config = kw.get("config", {}).get(pool_type, {})
        multi_pool_config = getdict(rbd_config)

        for pool, pool_config in multi_pool_config.items():
            multi_image_config = getdict(pool_config)
            image_config = {k: v for k, v in multi_image_config.items()}
            namespace = pool_config.get("namespace")
            remote_namespace = pool_config.get("remote_namespace")

            for image, image_config_val in image_config.items():
                image_enable_config = {
                    "pool": pool,
                    "image": image,
                    "mirrormode": "snapshot",
                    "namespace": namespace,
                    "remote_namespace": remote_namespace,
                }
                # Enable snapshot mode mirroring on images of the namespace
                enable_image_mirroring(pri_config, sec_config, **image_enable_config)
                # Verify image mirroring status on primary cluster
                wait_for_status(
                    rbd=rbd_primary,
                    cluster_name=pri_config.get("cluster").name,
                    imagespec=f"{pool}/{namespace}/{image}",
                    state_pattern="up+stopped",
                )
                # Verify image mirroring status on secondary cluster
                wait_for_status(
                    rbd=rbd_secondary,
                    cluster_name=sec_config.get("cluster").name,
                    imagespec=f"{pool}/{image}",
                    state_pattern="up+replaying",
                )
                if image_config_val.get(
                    "snap_schedule_levels"
                ) and image_config_val.get("snap_schedule_intervals"):
                    for level, interval in zip(
                        image_config_val["snap_schedule_levels"],
                        image_config_val["snap_schedule_intervals"],
                    ):
                        snap_schedule_config = {
                            "pool": pool,
                            "image": image,
                            "level": level,
                            "interval": interval,
                            "namespace": namespace,
                        }
                        # Adding snapshot schedules to the images in namespace
                        out, err = add_snapshot_scheduling(
                            rbd_primary, **snap_schedule_config
                        )
                        if err:
                            raise Exception(
                                "Adding snapshot schedule failed with error " + err
                            )
                    # Verify snapshot schedules are effective on the namespaces
                    verify_namespace_snapshot_schedule(
                        rbd_primary, pool, namespace, interval=interval, image=image
                    )

                pri_image_spec = f"{pool}/{namespace}/{image}"
                sec_image_spec = f"{pool}/{image}"
                # Write data on the primary image
                fio = kw.get("config", {}).get("fio", {})
                io_config = {
                    "size": fio["size"],
                    "do_not_create_image": True,
                    "num_jobs": fio["ODF_CONFIG"]["num_jobs"],
                    "iodepth": fio["ODF_CONFIG"]["iodepth"],
                    "rwmixread": fio["ODF_CONFIG"]["rwmixread"],
                    "direct": fio["ODF_CONFIG"]["direct"],
                    "invalidate": fio["ODF_CONFIG"]["invalidate"],
                    "config": {
                        "file_size": fio["size"],
                        "file_path": ["/mnt/mnt_" + random_string(len=5) + "/file"],
                        "get_time_taken": True,
                        "operations": {
                            "fs": "ext4",
                            "io": True,
                            "mount": True,
                            "map": True,
                        },
                        "cmd_timeout": 2400,
                        "io_type": fio["ODF_CONFIG"]["io_type"],
                    },
                }
                io_config["rbd_obj"] = rbd_primary
                io_config["client"] = client_primary
                image_spec = []
                image_spec.append(pri_image_spec)
                io_config["config"]["image_spec"] = image_spec
                (io, err) = krbd_io_handler(**io_config)
                if err:
                    raise Exception(
                        f"Map, mount and run IOs failed for {io_config['config']['image_spec']}"
                    )
                else:
                    log.info(
                        f"Map, mount and IOs successful for {io_config['config']['image_spec']}"
                    )
                time.sleep(int(interval[:-1]) * 120)
                # Verify data is mirrored from the primary to the secondary cluster using du command
                prim_usage = rbd_primary.image_usage(
                    **{"image-spec": pri_image_spec, "format": "json"}
                )
                sec_usage = rbd_secondary.image_usage(
                    **{"image-spec": sec_image_spec, "format": "json"}
                )
                if (
                    ast.literal_eval(prim_usage[0])["images"][0]["used_size"]
                    != ast.literal_eval(sec_usage[0])["images"][0]["used_size"]
                ):
                    raise Exception(
                        "Mirrored image usage sizes are not same on primary and secondary"
                    )
                # Verify the data on mirrored images is consistent
                data_integrity_spec = {
                    "first": {
                        "image_spec": pri_image_spec,
                        "rbd": rbd_primary,
                        "client": client_primary,
                        "file_path": f"/tmp/{random_string(len=3)}",
                    },
                    "second": {
                        "image_spec": sec_image_spec,
                        "rbd": rbd_secondary,
                        "client": client_secondary,
                        "file_path": f"/tmp/{random_string(len=3)}",
                    },
                }
                if check_data_integrity(**data_integrity_spec):
                    raise Exception("Data integrity check failed for " + pri_image_spec)
                log.info(
                    "Data is consistent with the mirrored image for " + pri_image_spec
                )
            # create image in the default namespace on secondary
            log.info(
                "Creating image in the default namespace of secondary and "
                + "mirroring to non-default namespace on primary.."
            )
            image = "image_" + random_string(len=4)
            out, err = rbd_secondary.create(
                **{"image-spec": f"{pool}/{image}", "size": 1024}
            )
            if err:
                raise Exception(f"Create image {pool}/{image} failed with error {err}")
            else:
                log.info(
                    "Created image "
                    + image
                    + " in default namespace of "
                    + pool
                    + "on cluster 2"
                )

            image_enable_config = {
                "pool": pool,
                "image": image,
                "mirrormode": "snapshot",
                "namespace": pool_config.get("remote_namespace"),
                "remote_namespace": pool_config.get("namespace"),
            }
            # Enable snapshot mode mirroring on images
            enable_image_mirroring(sec_config, pri_config, **image_enable_config)
            log.info(
                "Enabled image mirroring on " + pool + "/" + image + " in Cluster2"
            )

            snap_schedule_config = {
                "pool": pool,
                "image": image,
                "level": level,
                "interval": interval,
            }
            # Add snapshot schedules on the images on secondary side
            out, err = add_snapshot_scheduling(rbd_secondary, **snap_schedule_config)
            if verify_snapshot_schedule(rbd_secondary, pool, image, interval=interval):
                raise Exception(
                    "Snapshot schedule verification " + pool + "/" + image + " failed"
                )
            pri_image_spec = f"{pool}/{image}"
            sec_image_spec = f"{pool}/{namespace}/{image}"
            io_config["rbd_obj"] = rbd_secondary
            io_config["client"] = client_secondary
            image_spec = []
            image_spec.append(pri_image_spec)
            io_config["config"]["image_spec"] = image_spec
            # Write data on images created on secondary side
            (io, err) = krbd_io_handler(**io_config)
            if err:
                raise Exception(
                    f"Map, mount and run IOs failed for {io_config['config']['image_spec']}"
                )
            else:
                log.info(
                    f"Map, mount and IOs successful for {io_config['config']['image_spec']}"
                )
            time.sleep(int(interval[:-1]) * 120)
            # Verify the data on mirrored images is consistent
            data_integrity_spec = {
                "first": {
                    "image_spec": pri_image_spec,
                    "rbd": rbd_secondary,
                    "client": client_secondary,
                    "file_path": f"/tmp/{random_string(len=3)}",
                },
                "second": {
                    "image_spec": sec_image_spec,
                    "rbd": rbd_primary,
                    "client": client_primary,
                    "file_path": f"/tmp/{random_string(len=3)}",
                },
            }
            if check_data_integrity(**data_integrity_spec):
                raise Exception("Data integrity check failed for " + pri_image_spec)
    return 0


def run(**kw):
    """
    Test to verify non-default to default namespace mirroring
    Args:
        kw: Key/value pairs of configuration information to be used in the test
            Example::
          config:
            rep_pool_config:
              num_pools: 1
              num_images: 1
              do_not_create_image: True
              size: 1G
              mode: image
              mirror_level: namespace
              namespace_mirror_type: non-default_to_default
              mirrormode: snapshot
              snap_schedule_levels:
                - namespace
              snap_schedule_intervals:
                - 1m
    """
    try:
        mirror_obj = initial_mirror_config(**kw)
        mirror_obj.pop("output", [])
        for val in mirror_obj.values():
            if not val.get("is_secondary", False):
                pri_config = val
            else:
                sec_config = val
        log.info("Initial configuration complete")
        pool_types = list(mirror_obj.values())[0].get("pool_types")

        test_non_default_to_default_namespace_mirroring(
            pri_config, sec_config, pool_types, **kw
        )
        log.info("Test non-default to default namespace mirroring passed")
    except Exception as e:
        log.error(
            f"Testing non-default to default namespace mirroring failed with error {str(e)}"
        )
        return 1
    finally:
        cleanup(pool_types=pool_types, multi_cluster_obj=mirror_obj, **kw)
    return 0
