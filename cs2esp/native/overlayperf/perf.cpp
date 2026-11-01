// Batched bone transforms, projection and context-owned GL submission for the overlay.
#include <cmath>
#include <cstdint>

extern "C" {

// Convert position/quaternion slabs (8 floats per bone) to world transforms (12 floats per bone).
__declspec(dllexport) void op_quat_world(const float *slabs, int players, int bones, float *out) {
	const long long total = 1LL * players * bones;
	#pragma omp parallel for schedule(static) if(total >= 16384)
	for (long long index = 0; index < total; ++index) {
		const float *slab = slabs + index * 8;
		float *transform = out + index * 12;
		const float quat_x = slab[4], quat_y = slab[5], quat_z = slab[6], quat_w = slab[7];
		const float norm = quat_x * quat_x + quat_y * quat_y + quat_z * quat_z + quat_w * quat_w;
		const float scale = norm > 0.0f ? 2.0f / norm : 0.0f;
		const float scaled_x = quat_x * scale, scaled_y = quat_y * scale, scaled_z = quat_z * scale;
		const float product_wx = quat_w * scaled_x, product_wy = quat_w * scaled_y, product_wz = quat_w * scaled_z;
		const float product_xx = quat_x * scaled_x, product_xy = quat_x * scaled_y, product_xz = quat_x * scaled_z;
		const float product_yy = quat_y * scaled_y, product_yz = quat_y * scaled_z, product_zz = quat_z * scaled_z;
		transform[0] = 1.0f - (product_yy + product_zz);
		transform[1] = product_xy - product_wz;
		transform[2] = product_xz + product_wy;
		transform[3] = slab[0];
		transform[4] = product_xy + product_wz;
		transform[5] = 1.0f - (product_xx + product_zz);
		transform[6] = product_yz - product_wx;
		transform[7] = slab[1];
		transform[8] = product_xz - product_wy;
		transform[9] = product_yz + product_wx;
		transform[10] = 1.0f - (product_xx + product_yy);
		transform[11] = slab[2];
	}
}

// Project XYZ points using Source 2's row-major clip matrix; points behind the camera produce NaN.
__declspec(dllexport) void op_project(const float *pts, int count, const float *view, float half_w, float half_h, float *out) {
	const float m00 = view[0], m01 = view[1], m02 = view[2], m03 = view[3];
	const float m10 = view[4], m11 = view[5], m12 = view[6], m13 = view[7];
	const float m30 = view[12], m31 = view[13], m32 = view[14], m33 = view[15];
	#pragma omp parallel for schedule(static) if(count >= 512)
	for (int index = 0; index < count; ++index) {
		const float point_x = pts[index * 3], point_y = pts[index * 3 + 1], point_z = pts[index * 3 + 2];
		const float clip_w = m30 * point_x + m31 * point_y + m32 * point_z + m33;
		if (clip_w < 0.001f) {
			out[index * 2] = out[index * 2 + 1] = nanf("");
			continue;
		}
		const float inverse_w = 1.0f / clip_w;
		out[index * 2] = half_w + (m00 * point_x + m01 * point_y + m02 * point_z + m03) * inverse_w * half_w;
		out[index * 2 + 1] = half_h - (m10 * point_x + m11 * point_y + m12 * point_z + m13) * inverse_w * half_h;
	}
}

// Multiply world transforms by per-model inverse binds for CPU skinning and benchmarks.
__declspec(dllexport) void op_skin(const float *world, const float *inv_bind, int players, int bones, float *out) {
	const long long total = 1LL * players * bones;
	#pragma omp parallel for schedule(static) if(total >= 16384)
	for (long long index = 0; index < total; ++index) {
		const int bone_index = (int)(index % bones);
		const float *world_transform = world + index * 12;
		const float *inverse_bind = inv_bind + 1LL * bone_index * 12;
		float *skin_transform = out + index * 12;
		for (int row = 0; row < 3; ++row) {
			const float axis_x = world_transform[row * 4];
			const float axis_y = world_transform[row * 4 + 1];
			const float axis_z = world_transform[row * 4 + 2];
			const float translation = world_transform[row * 4 + 3];
			skin_transform[row * 4] = axis_x * inverse_bind[0] + axis_y * inverse_bind[4] + axis_z * inverse_bind[8];
			skin_transform[row * 4 + 1] = axis_x * inverse_bind[1] + axis_y * inverse_bind[5] + axis_z * inverse_bind[9];
			skin_transform[row * 4 + 2] = axis_x * inverse_bind[2] + axis_y * inverse_bind[6] + axis_z * inverse_bind[10];
			skin_transform[row * 4 + 3] = axis_x * inverse_bind[3] + axis_y * inverse_bind[7] + axis_z * inverse_bind[11] + translation;
		}
	}
}

// Report whether this build includes OpenMP support.
__declspec(dllexport) int op_threads() {
	#ifdef _OPENMP
	return 1;
	#else
	return 0;
	#endif
}

} // extern "C"

// The Python owner retains function pointers and payload arrays until the batch returns.
namespace {
using GLenum = unsigned int;
using GLuint = unsigned int;
using GLint = int;
using GLsizei = int;
using GLboolean = unsigned char;
using Matrix4 = void (__stdcall *)(GLint, GLsizei, GLboolean, const float *);
using Vectors4 = void (__stdcall *)(GLint, GLsizei, const float *);
using Vector4 = void (__stdcall *)(GLint, float, float, float, float);
using Vector3 = void (__stdcall *)(GLint, float, float, float);
using Scalar1 = void (__stdcall *)(GLint, float);
using BindVao = void (__stdcall *)(GLuint);
using DrawElements = void (__stdcall *)(GLenum, GLsizei, GLenum, const void *);
using Program = void (__stdcall *)(GLuint);
using Enum1 = void (__stdcall *)(GLenum);
using Blend = void (__stdcall *)(GLenum, GLenum);
using DepthMask = void (__stdcall *)(GLboolean);

constexpr int kMaxBones = 384;
constexpr GLenum kGlBlend = 0x0BE2;
constexpr GLenum kGlSrcAlpha = 0x0302;
constexpr GLenum kGlOneMinusSrcAlpha = 0x0303;
constexpr GLenum kGlDepthTest = 0x0B71;
constexpr GLenum kGlLess = 0x0201;
constexpr GLenum kGlCullFace = 0x0B44;
constexpr GLenum kGlDepthBufferBit = 0x00000100;
constexpr GLenum kGlTriangles = 0x0004;
constexpr GLenum kGlUnsignedInt = 0x1405;

struct ChamsApi {
	Matrix4 matrix4;
	Vectors4 vectors4;
	Vector4 vector4;
	Vector3 vector3;
	Scalar1 scalar1;
	BindVao bind_vao;
	DrawElements draw;
	Program program;
	Enum1 enable;
	Blend blend;
	DepthMask depth_mask;
	Enum1 depth_func;
	Enum1 disable;
	Enum1 clear;
};

struct ChamsDraw {
	const float *view;
	const float *bones;
	const float *inverse;
	std::uint32_t vao;
	std::int32_t index_count;
	std::uint32_t flags;
	float colour[4];
	float camera[3];
	float rim;
};

static_assert(sizeof(ChamsDraw) == 72);
static_assert(sizeof(ChamsApi) == 14 * sizeof(void *));
}

extern "C" {
__declspec(dllexport) int op_chams_draw_size() { return sizeof(ChamsDraw); }
__declspec(dllexport) int op_chams_api_size() { return sizeof(ChamsApi); }

// Validate every job before touching GL, submit in order, then restore the existing post-batch state.
__declspec(dllexport) int op_chams_draw_batch(const ChamsApi *api, GLuint program, const GLint *loc, const ChamsDraw *jobs, int count) {
	if (!api || !loc || !jobs || !program || count < 1 || count > 16384
			|| !api->matrix4 || !api->vectors4 || !api->vector4 || !api->vector3 || !api->scalar1 || !api->bind_vao || !api->draw || !api->program
			|| !api->enable || !api->blend || !api->depth_mask || !api->depth_func || !api->disable || !api->clear) return -1;
	for (int index = 0; index < count; ++index) {
		const ChamsDraw &job = jobs[index];
		if (!job.bones || job.index_count < 0 || ((job.flags & 1) && !job.view) || ((job.flags & 2) && !job.inverse)) return -1;
	}
	api->program(program);
	api->enable(kGlBlend);
	api->blend(kGlSrcAlpha, kGlOneMinusSrcAlpha);
	api->enable(kGlDepthTest);
	api->depth_mask(1);
	api->depth_func(kGlLess);
	api->disable(kGlCullFace);
	api->clear(kGlDepthBufferBit);
	for (int index = 0; index < count; ++index) {
		const ChamsDraw &job = jobs[index];
		if (job.flags & 1) api->matrix4(loc[0], 1, 1, job.view);
		if (job.flags & 2) api->vectors4(loc[2], kMaxBones, job.inverse);
		api->vectors4(loc[1], kMaxBones, job.bones);
		if (job.flags & 4) api->vector4(loc[3], job.colour[0], job.colour[1], job.colour[2], job.colour[3]);
		if (job.flags & 8) api->vector3(loc[4], job.camera[0], job.camera[1], job.camera[2]);
		if (job.flags & 16) api->scalar1(loc[5], job.rim);
		if (job.flags & 32) api->bind_vao(job.vao);
		api->draw(kGlTriangles, job.index_count, kGlUnsignedInt, nullptr);
	}
	api->bind_vao(0);
	api->program(0);
	api->disable(kGlDepthTest);
	api->depth_mask(1);
	return count;
}
}
