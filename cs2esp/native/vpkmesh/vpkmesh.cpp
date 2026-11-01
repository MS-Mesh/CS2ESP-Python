// C ABI over the MS Mesh C++ Source 2 decoders (source2.hpp / vpk.hpp).
//
// Reads CS2's pak01_dir.vpk from disk only. It never opens the game process;
// the Python overlay feeds bone matrices from its own ReadProcessMemory path.
//
// Output is one skinned, GPU-ready mesh per agent model: interleaved vertices
// (pos3, normal3, joints4 u16, weights4 f32 = 48 bytes), u32 indices, and the
// model's inverse bind poses (row-major 3x4) plus bone parents.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "vpk.hpp"
#include "source2.hpp"

#define VM_API extern "C" __declspec(dllexport)

namespace {

struct Vertex {
	float px, py, pz;
	float nx, ny, nz;
	std::uint16_t joints[4];
	float weights[4];
};
static_assert(sizeof(Vertex) == 48, "Vertex layout is shared with Python");

struct Model {
	std::vector<Vertex> vertices;
	std::vector<std::uint32_t> indices;
	std::vector<float> inv_bind; // bone_count * 12, row-major 3x4
	std::vector<std::int16_t> parents;
	std::string error;
};

vpk::VPKDir g_vpk;
std::mutex g_vpk_lock;
std::string g_last_error;

std::string normalize_path(std::string path, const char *src_ext, const char *cmp_ext) {
	for (char &c : path) {
		if (c == '\\') c = '/';
		else if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
	}
	const std::string se = src_ext;
	const std::string ce = cmp_ext;
	if (path.size() >= ce.size() && path.compare(path.size() - ce.size(), ce.size(), ce) == 0) return path;
	if (path.size() >= se.size() && path.compare(path.size() - se.size(), se.size(), se) == 0) return path + "_c";
	return path + ce;
}

// Rebuild smooth vertex normals from the appended triangle range.
void recalc_normals(std::vector<Vertex> &vertices, const std::vector<std::uint32_t> &indices, std::size_t base_vertex) {
	for (std::size_t index = base_vertex; index < vertices.size(); ++index)
		vertices[index].nx = vertices[index].ny = vertices[index].nz = 0.f;
	for (std::size_t index = 0; index + 2 < indices.size(); index += 3) {
		const auto first_index = indices[index], second_index = indices[index + 1], third_index = indices[index + 2];
		if (first_index < base_vertex || second_index < base_vertex || third_index < base_vertex) continue;
		if (first_index >= vertices.size() || second_index >= vertices.size() || third_index >= vertices.size()) continue;
		const auto &first_vertex = vertices[first_index];
		const auto &second_vertex = vertices[second_index];
		const auto &third_vertex = vertices[third_index];
		const float edge1_x = second_vertex.px - first_vertex.px, edge1_y = second_vertex.py - first_vertex.py, edge1_z = second_vertex.pz - first_vertex.pz;
		const float edge2_x = third_vertex.px - first_vertex.px, edge2_y = third_vertex.py - first_vertex.py, edge2_z = third_vertex.pz - first_vertex.pz;
		const float cross_x = edge1_y * edge2_z - edge1_z * edge2_y;
		const float cross_y = edge1_z * edge2_x - edge1_x * edge2_z;
		const float cross_z = edge1_x * edge2_y - edge1_y * edge2_x;
		for (auto vertex_index : {first_index, second_index, third_index}) {
			vertices[vertex_index].nx += cross_x;
			vertices[vertex_index].ny += cross_y;
			vertices[vertex_index].nz += cross_z;
		}
	}
	for (std::size_t index = base_vertex; index < vertices.size(); ++index) {
		auto &vertex = vertices[index];
		const float normal_length = std::sqrt(vertex.nx * vertex.nx + vertex.ny * vertex.ny + vertex.nz * vertex.nz);
		if (normal_length > 1e-6f) {
			vertex.nx /= normal_length;
			vertex.ny /= normal_length;
			vertex.nz /= normal_length;
		} else {
			vertex.nx = 0.f;
			vertex.ny = 0.f;
			vertex.nz = 1.f;
		}
	}
}

// Shared VB/IB shape preconditions; attribute presence stays with the individual draw paths
bool layout_ok(const source2::VertexBuffer &vb, const source2::IndexBuffer &ib) {
	return vb.vertex_size > 0 && vb.vertex_count > 0 && (ib.index_size == 2 || ib.index_size == 4) &&
		static_cast<std::size_t>(ib.index_count) <= ib.data.size() / ib.index_size &&
		static_cast<std::size_t>(vb.vertex_count) <= vb.data.size() / vb.vertex_size;
}

// Port of ModelCache::append_geometry without the texture / paint-kit work.
bool append_geometry(const source2::ModelData &md, Model &out, std::string &error) {
	const std::size_t skel_size = md.skeleton.size();
	std::vector<source2::MeshDrawCall> draws = md.draw_calls;
	if (draws.empty()) {
		if (md.vertex_buffers.size() == md.index_buffers.size()) {
			for (std::size_t i = 0; i < md.vertex_buffers.size(); ++i) {
				const auto &vb = md.vertex_buffers[i];
				const auto &ib = md.index_buffers[i];
				const bool drawable = layout_ok(vb, ib) && vb.find_attr("POSITION") && vb.find_attr("BLENDINDICES") && vb.find_attr("BLENDWEIGHT") &&
					ib.index_count >= 3 && ib.index_count % 3 == 0;
				if (!drawable) continue;
				source2::MeshDrawCall draw{};
				draw.vertex_buffer = static_cast<std::uint32_t>(i);
				draw.index_buffer = static_cast<std::uint32_t>(i);
				draw.index_count = ib.index_count;
				draws.push_back(draw);
			}
		}
		if (draws.empty()) {
			error = "no draw metadata";
			return false;
		}
	} else {
		// Default mesh group only: agents also ship first-person arms/sleeves.
		std::vector<source2::MeshDrawCall> kept;
		for (const auto &d : draws)
			if (md.mesh_in_default_group(d.mesh_index)) kept.push_back(d);
		if (!kept.empty()) draws = std::move(kept);
	}

	bool appended = false;
	for (const auto &draw : draws) {
		if (draw.vertex_buffer >= md.vertex_buffers.size() || draw.index_buffer >= md.index_buffers.size()) continue;
		const auto &vb = md.vertex_buffers[draw.vertex_buffer];
		const auto &ib = md.index_buffers[draw.index_buffer];
		if (draw.index_count == 0 || draw.index_count % 3 != 0 || draw.start_index > ib.index_count || draw.index_count > ib.index_count - draw.start_index) continue;
		if (!layout_ok(vb, ib)) continue;

		const auto *pos_attr = vb.find_attr("POSITION");
		const auto *norm_attr = vb.find_attr("NORMAL");
		const auto *bi_attr = vb.find_attr("BLENDINDICES");
		const auto *bw_attr = vb.find_attr("BLENDWEIGHT");
		if (!pos_attr || !bi_attr || !bw_attr || skel_size == 0) continue;

		const std::vector<int> *remap = nullptr;
		if (draw.vertex_buffer < md.per_vb_remap.size() && !md.per_vb_remap[draw.vertex_buffer].empty()) remap = &md.per_vb_remap[draw.vertex_buffer];
		else if (!md.remapping_table.empty()) remap = &md.remapping_table;

		const std::size_t base = out.vertices.size();
		bool normals_ok = norm_attr != nullptr;
		bool part_ok = true;
		out.vertices.reserve(base + vb.vertex_count);
		for (std::uint32_t vi = 0; vi < vb.vertex_count; ++vi) {
			Vertex v{};
			const auto p = source2::read_attr_vec3(vb, vi, *pos_attr);
			v.px = p.x;
			v.py = p.y;
			v.pz = p.z;
			if (norm_attr) {
				if (const auto n = source2::read_attr_normal(vb, vi, *norm_attr)) {
					v.nx = n->x;
					v.ny = n->y;
					v.nz = n->z;
				} else {
					normals_ok = false;
				}
			}
			const auto raw = remap ? source2::read_attr_blend_indices(vb, vi, *bi_attr, remap) : source2::read_attr_blend_indices(vb, vi, *bi_attr);
			for (int k = 0; k < 4; ++k) {
				if (raw[k] >= skel_size) {
					part_ok = false;
					break;
				}
				v.joints[k] = raw[k];
			}
			if (!part_ok) break;
			const auto w = source2::read_attr_blend_weights(vb, vi, *bw_attr);
			float sum = 0.f;
			for (float x : w)
				sum += (std::isfinite(x) && x > 0.f) ? x : 0.f;
			if (sum <= 1e-4f) {
				v.weights[0] = 1.f;
			} else
				for (int k = 0; k < 4; ++k)
					v.weights[k] = (std::isfinite(w[k]) && w[k] > 0.f) ? w[k] / sum : 0.f;
			out.vertices.push_back(v);
		}
		if (!part_ok) {
			out.vertices.resize(base);
			continue;
		}

		const std::size_t index_base = out.indices.size();
		bool indices_ok = true;
		for (std::uint32_t cursor = 0; cursor < draw.index_count; ++cursor) {
			const std::size_t src = static_cast<std::size_t>(draw.start_index) + cursor;
			std::uint32_t raw = 0;
			if (ib.index_size == 2) {
				std::uint16_t v16 = 0;
				std::memcpy(&v16, ib.data.data() + src * 2, 2);
				raw = v16;
			} else {
				std::memcpy(&raw, ib.data.data() + src * 4, 4);
			}
			const std::int64_t resolved = static_cast<std::int64_t>(raw) + draw.base_vertex;
			if (resolved < 0 || resolved >= vb.vertex_count) {
				indices_ok = false;
				break;
			}
			out.indices.push_back(static_cast<std::uint32_t>(base + resolved));
		}
		if (!indices_ok) {
			out.indices.resize(index_base);
			out.vertices.resize(base);
			continue;
		}
		if (!normals_ok) {
			std::vector<std::uint32_t> part_idx(out.indices.begin() + index_base, out.indices.end());
			recalc_normals(out.vertices, part_idx, base);
		}
		appended = true;
	}
	if (!appended) error = "no drawable skinned triangles";
	return appended;
}

bool load_model(const std::string &model_path, Model &out) {
	const std::string key = normalize_path(model_path, ".vmdl", ".vmdl_c");
	auto bytes = g_vpk.read_file(key);
	if (!bytes) {
		out.error = "not found: " + key;
		return false;
	}
	auto md = source2::parse_vmdl_c(bytes->data(), bytes->size());
	if (!md) {
		out.error = "vmdl_c parse failed";
		return false;
	}
	if (md->skeleton.empty() || md->inv_bind_poses.size() < md->skeleton.size()) {
		out.error = "model has no skeleton";
		return false;
	}

	std::size_t decoded = 0;
	for (const auto &ref : md->mesh_resources) {
		const std::string mesh_path = normalize_path(ref, ".vmesh", ".vmesh_c");
		auto mesh_bytes = g_vpk.read_file(mesh_path);
		if (!mesh_bytes) continue;
		auto vbib = source2::parse_vmesh_c(mesh_bytes->data(), mesh_bytes->size());
		if (!vbib || vbib->vbs.empty() || vbib->ibs.empty()) continue;
		source2::ModelData part = *md;
		part.vertex_buffers = std::move(vbib->vbs);
		part.index_buffers = std::move(vbib->ibs);
		part.draw_calls = std::move(vbib->draws);
		part.remapping_table.clear();
		part.remapping_table_starts.clear();
		source2::extract_vmesh_c_remap(mesh_bytes->data(), mesh_bytes->size(), part);
		if (part.remapping_table.empty()) {
			part.remapping_table = md->remapping_table;
			part.remapping_table_starts = md->remapping_table_starts;
		}
		source2::build_per_vb_remaps(part);
		std::string err;
		if (append_geometry(part, out, err)) ++decoded;
	}
	if (decoded == 0 && md->has_geometry()) {
		source2::ModelData embedded = *md;
		source2::build_per_vb_remaps(embedded);
		std::string err;
		if (append_geometry(embedded, out, err)) ++decoded;
		else out.error = err;
	}
	if (decoded == 0 || out.indices.empty()) {
		if (out.error.empty()) out.error = "no geometry";
		return false;
	}

	const std::size_t bones = md->skeleton.size();
	out.inv_bind.resize(bones * 12);
	out.parents.resize(bones);
	for (std::size_t i = 0; i < bones; ++i) {
		const auto &m = md->inv_bind_poses[i];
		for (int r = 0; r < 3; ++r)
			for (int c = 0; c < 4; ++c)
				out.inv_bind[i * 12 + r * 4 + c] = m.mat[r][c];
		out.parents[i] = static_cast<std::int16_t>(md->skeleton[i].parent);
	}
	return true;
}

} // namespace

VM_API int vm_open(const char *dir_vpk_path) {
	std::scoped_lock lock(g_vpk_lock);
	if (dir_vpk_path && *dir_vpk_path) {
		if (g_vpk.open(dir_vpk_path)) return 1;
		g_last_error = std::string("cannot open ") + dir_vpk_path;
		return 0;
	}
	for (const auto &candidate : vpk::cs2_default_vpk_paths())
		if (g_vpk.open(candidate)) return 1;
	g_last_error = "pak01_dir.vpk not found";
	return 0;
}

VM_API void *vm_load(const char *model_path) {
	auto model = std::make_unique<Model>();
	try {
		std::scoped_lock lock(g_vpk_lock);
		if (!g_vpk.is_open()) {
			g_last_error = "vpk not open";
			return nullptr;
		}
		if (!load_model(model_path ? model_path : "", *model)) {
			g_last_error = model->error;
			return nullptr;
		}
	} catch (const std::exception &e) {
		g_last_error = e.what();
		return nullptr;
	} catch (...) {
		g_last_error = "decoder exception";
		return nullptr;
	}
	return model.release();
}

VM_API std::uint32_t vm_vertex_count(void *h) { return h ? static_cast<std::uint32_t>(static_cast<Model *>(h)->vertices.size()) : 0; }
VM_API std::uint32_t vm_index_count(void *h) { return h ? static_cast<std::uint32_t>(static_cast<Model *>(h)->indices.size()) : 0; }
VM_API std::uint32_t vm_bone_count(void *h) { return h ? static_cast<std::uint32_t>(static_cast<Model *>(h)->parents.size()) : 0; }

VM_API void vm_copy(void *h, void *vertices, std::uint32_t *indices, float *inv_bind, std::int16_t *parents) {
	if (!h) return;
	const auto *m = static_cast<Model *>(h);
	if (vertices) std::memcpy(vertices, m->vertices.data(), m->vertices.size() * sizeof(Vertex));
	if (indices) std::memcpy(indices, m->indices.data(), m->indices.size() * sizeof(std::uint32_t));
	if (inv_bind) std::memcpy(inv_bind, m->inv_bind.data(), m->inv_bind.size() * sizeof(float));
	if (parents) std::memcpy(parents, m->parents.data(), m->parents.size() * sizeof(std::int16_t));
}

VM_API void vm_free(void *h) { delete static_cast<Model *>(h); }

VM_API const char *vm_last_error() { return g_last_error.c_str(); }