import copy

import numpy as np

from .fix_mesh import fix_mesh, simp_vol, simp_qual
from . import edges

__all__ = [
    "make_mesh_boundaries_traversable",
    "delete_interior_faces",
    "delete_exterior_faces",
]


def _arg_sortrows(arr):
    """Before a multi column sort like MATLAB's sortrows"""
    i = arr[:, 1].argsort()  # First sort doesn't need to be stable.
    j = arr[i, 0].argsort(kind="mergesort")
    return i[j]


def _face_to_face(t):
    """Face to face connectivity table.
        Face `i` is connected to faces `ftof[ix[i]:ix[i+1], 1]`
        By connected, I mean shares a mutual edge.

    Parameters
    ----------
    t: array-like
        Mesh connectivity table.

    Returns
    -------
    ftof: array-like
        Face numbers connected to faces.
    ix: array-like
        indices into `ftof`

    """
    nt = len(t)
    t = np.sort(t, axis=1)
    e = t[:, [[0, 1], [0, 2], [1, 2]]].reshape((nt * 3, 2))
    trinum = np.repeat(np.arange(nt), 3)
    j = _arg_sortrows(e)
    e = e[j, :]
    trinum = trinum[j]
    k = np.argwhere(~np.diff(e, axis=0).any(axis=1))
    ftof = np.concatenate((trinum[k], trinum[k + 1]), axis=1)
    dmy1 = ftof[:, 0].argsort()
    dmy2 = ftof[:, 1].argsort()
    tmp = np.vstack(
        (
            ftof[dmy1, :],
            np.fliplr(ftof[dmy2]),
            np.column_stack((np.arange(nt), np.arange(nt))),
        )
    )
    j = _arg_sortrows(tmp)
    ftof = tmp[j, :]
    ix = np.argwhere(np.diff(ftof[:, 0])) + 1
    ix = np.insert(ix, 0, 0)
    ix = np.append(ix, len(ftof))
    return ftof, ix


def _vertex_to_face(vertices, faces):
    """Determine which faces are connected to which vertices.

     Parameters
    ----------
    vertices: array-like
        Vertices of the mesh.
    faces: array-like
        Mesh connectivity table.

    Returns
    -------
    vtoc: array-like
        face numbers connected to vertices.
    ix: array-like
        indices into `vtoc`

    """
    num_faces = len(faces)

    ext = np.tile(np.arange(0, num_faces), (3, 1)).reshape(-1, order="F")
    ve = np.reshape(faces, (-1,))
    ve = np.vstack((ve, ext)).T
    ve = ve[ve[:, 0].argsort(), :]

    idx = np.insert(np.diff(ve[:, 0]), 0, 0)
    vtoc_pointer = np.argwhere(idx)
    vtoc_pointer = np.insert(vtoc_pointer, 0, 0)
    vtoc_pointer = np.append(vtoc_pointer, num_faces * 3)

    vtoc = ve[:, 1]

    return vtoc, vtoc_pointer


def make_mesh_boundaries_traversable(vertices, faces, dj_cutoff=0.05):
    """
    A mesh described by vertices and faces is  "cleaned" and returned.
    Alternates between checking "interior" and "exterior" portions
    of the mesh until convergence is obtained. Convergence is defined as:
    having no vertices connected to more than two boundary edges.

    Parameters
    ----------
    vertices: array-like
        The vertices of the "uncleaned" mesh.
    faces: array-like
        The "uncleaned" mesh connectivity.
    dj_cutoff: float
        A decimal percentage (max 1.0) used to decide whether to keep or remove
        disconnected portions of the meshing domain.


    Returns
    -------
    vertices: array-like
        The vertices of the "cleaned" mesh.

    faces: array-like
        The "cleaned" mesh connectivity.

    Notes
    -----

    Interior Check: Deletes faces that are within the interior of the
    mesh so that no vertices are connected to more than two boundary edges. For
    example, a barrier island could become very thin in a middle portion so that you
    have a vertex connected to two faces but four boundary edges, in a
    bow-tie type formation.

    This code will delete one of those connecting
    faces to ensure the spit is `clean` in the sense that two boundary edges
    are connected to that vertex. In the case of a choice between faces to
    delete, the one with the lowest quality is chosen.

    Exterior Check: Finds small disjoint portions of the mesh and removes
    them using a depth-first search. The individual disjoint portions are
    removed based on `dj_cutoff` which is a decimal representing a fractional
    threshold component of the total mesh.

    """

    boundary_edges, boundary_vertices = _external_topology(vertices, faces)

    # NB: when this inequality is not met, the mesh boundary is  not valid and non-manifold
    while len(boundary_edges) > len(boundary_vertices):

        faces = delete_exterior_faces(vertices, faces, dj_cutoff)
        vertices, faces, _ = fix_mesh(vertices, faces, delete_unused=True)

        faces, _ = delete_interior_faces(vertices, faces)
        vertices, faces, _ = fix_mesh(vertices, faces, delete_unused=True)

        boundary_edges, boundary_vertices = _external_topology(vertices, faces)

    return vertices, faces


def _external_topology(vertices, faces):
    """Get edges and vertices that make up the boundary of the mesh"""
    boundary_edges = edges.get_boundary_edges(faces)
    boundary_vertices = vertices[np.unique(boundary_edges.reshape(-1))]
    return boundary_edges, boundary_vertices


def delete_exterior_faces(vertices, faces, dj_cutoff):
    """Deletes portions of the mesh that are "outside" or not
    connected to the majority which represent a fractional
    area less than `dj_cutoff`.
    """
    t1 = copy.deepcopy(faces)
    t = np.array([])
    # Calculate the total area of the patch
    A = np.sum(simp_vol(vertices, faces))
    An = A
    # Based on area proportion
    while (An / A) > dj_cutoff:
        # Perform the depth-First-Search to get `nflag`
        nflag = _depth_first_search(vertices, t1)

        # Get new triangulation and its area
        t2 = t1[nflag == 1, :]
        An = np.sum(simp_vol(vertices, t2))

        # If large enough, retain this component
        if (An / A) > dj_cutoff:
            if len(t) == 0:
                t = t2
            else:
                t = np.concatenate((t, t2))

        # Delete where nflag == 1 from tmp t1 mesh
        t1 = np.delete(t1, nflag == 1, axis=0)
        print(f"ACCEPTED: Deleting {int(np.sum(nflag==0))} faces outside the main mesh")

        # Calculate the remaining area
        An = np.sum(simp_vol(vertices, t1))

    return t


def delete_interior_faces(vertices, faces):
    """Delete interior faces that have vertices with more than
    two vertices declared as boundary vertices
    """
    # Get updated boundary topology
    boundary_edges, boundary_vertices = _external_topology(vertices, faces)
    etbv = boundary_edges.reshape(-1)
    # Count how many edges a vertex appears in.
    uebtv, count = np.unique(etbv, return_counts=True)
    # Get the faces connected to the vertices
    vtoc, nne = _vertex_to_face(vertices, faces)
    # Vertices which appear more than twice (implying they are shared by
    # more than two boundary edges)
    del_face_idx = []
    for ix in uebtv[count > 2]:
        conn_faces = vtoc[nne[ix] : nne[ix + 1]]
        del_face = []
        for conn_face in conn_faces:
            II = etbv == faces[conn_face, 0]
            JJ = etbv == faces[conn_face, 1]
            KK = etbv == faces[conn_face, 2]
            if np.any(II) and np.any(JJ) and np.any(KK):
                del_face.append(conn_face)

        if len(del_face) == 1:
            del_face_idx.append(del_face[0])
        elif len(del_face) > 1:
            # Delete worst quality qualifying face.
            qual = simp_qual(vertices, faces[del_face])
            idx = np.argmin(qual)
            del_face_idx.append(del_face[idx])
        else:
            # No connected faces have all vertices on boundary edge so we
            # select the worst quality connecting face.
            qual = simp_qual(vertices, faces[conn_faces])
            idx = np.argmin(qual)
            del_face_idx.append(conn_faces[idx])

    print(f"ACCEPTED: Deleting {len(del_face_idx)} faces inside the main mesh")
    faces = np.delete(faces, del_face_idx, 0)

    return faces, del_face_idx


def _depth_first_search(points, faces):
    """Depth-First-Search (DFS) across the triangulation"""

    # Get graph connectivity.
    ftof, idx = _face_to_face(faces)

    nt = len(faces)

    # select a random face
    selected = np.random.randint(0, nt, 1)

    nflag = np.zeros(nt)

    searching = True

    visited = []
    visited.append(*selected)

    # Traverse through connected mesh
    while searching:
        searching = False
        for c in visited:
            # Flag the current face as visited
            nflag[c] = 1
            # Search connected faces
            neis = [nei for nei in ftof[idx[c] : idx[c + 1], 1]]
            # Flag connected faces as visited
            for nei in neis:
                if nflag[nei] == 0:
                    nflag[nei] = 1
                    visited.append(nei)
                    searching = True
    return nflag