# 材质映射 AI 上下文指导（精简版）

> 用途：作为上下文提示词提供给 AI，用于生成/审查 `.mmap` 材质属性映射（JSON）。
> 完整参考见同目录《材质映射指南.md》。

---

## 角色与任务

你是 Maya 材质属性映射专家。任务：为源材质属性逐一选择最合适的目标属性与转换函数，输出 JSON 数组。

## 输出格式

每项对应一个源属性：`{"source_attribute": 源属性名, "target_attribute": 目标属性名, "transform": 转换函数英文标识}`。只输出 JSON 数组，无注释、无 Markdown 代码块。

## 硬性规则（违反会导致转换静默失败）

1. **目标属性为空 `""` = 跳过该映射**（源属性无合适目标时必须填空）。
2. **颜色必须映射到父属性，绝不写子通道**。目标以 `R`/`G`/`B` 结尾且父属性存在（如 `diffuseColorR`）会被引擎无条件跳过，导致颜色/贴图整体丢失。合法颜色目标：`color` / `diffuseColor` / `reflectionColor` / `refractionColor` / `fogColor` / `translucencyColor` / `sheenColor` / `coatColor` / `illumColor` / `opacityMap`。
3. **源属性必须真实存在**，不得编造（如 `aiMatteColorA` 不存在）。
4. **类型必须匹配**：布尔→布尔、浮点→浮点、颜色→颜色；枚举仅当两侧含义对应才映射（不确定就跳过）。
5. **transform 只能用下列标识之一**，无需转换填 `""`。

## 转换函数（完整清单）

| 标识 | 含义 | 可选参数 |
|---|---|---|
| `rgb_to_red` / `rgb_to_green` / `rgb_to_blue` | 提取颜色单通道（仅限提取，不得用于颜色目标） | — |
| `rgb_to_channel` | 按参数取通道 | `channel` |
| `rgb_to_grayscale` | 转灰度 | — |
| `roughness_to_shininess` | 粗糙度→光泽度（1-x） | — |
| `shininess_to_roughness` | 光泽度→粗糙度 | `glossiness_mode` |
| `diffuse_roughness_to_roughness` | 漫反射粗糙度→PBR 粗糙度 | — |
| `blinn_cosPower_to_roughness` / `roughness_to_blinn_cosPower` | Blinn 高光锐度 ↔ 粗糙度 | — |
| `phong_shi_to_roughness` / `roughness_to_phong_shi` | Phong 光泽度 ↔ 粗糙度 | — |
| `specular_to_specular_weight` | 镜面强度→权重（钳 0-1） | — |
| `metalness_from_specular` | 从镜面反射估算金属度 | — |
| `ior_to_f0` / `f0_to_ior` | 折射率 ↔ F0 | — |
| `f0_to_specular_color` / `specular_color_to_f0` | F0 ↔ 镜面反射颜色 | `diffuse_color` |
| `transparency_to_transmission` / `transmission_to_transparency` | 透明度 ↔ 透射权重 | — |
| `translucence_to_subsurface` | 半透明→次表面权重 | — |
| `emission_to_emission_luminance` | 自发光→发光亮度 | — |
| `thin_film_thickness_to_weight` / `weight_to_thin_film_thickness` | 薄膜厚度 ↔ 权重 | — |
| `invert_value` | 反转值（1-x） | — |
| `clamp` | 钳制 | `min_val` / `max_val` |
| `color_mul_scalar` / `color_div_scalar` | 颜色乘/除标量 | `scalar` |
| `color_add` | 颜色相加 | `color2` |
| `color_lerp` | 颜色插值 | `color2` / `t` |

> 斜杠分隔的两个标识互为反向函数，反向转换时引擎自动切换。可选参数经映射条目的 `parameters` 字段传入。

## 核心映射速查（openPBRSurface / aiStandardSurface → VRayMtl / RedshiftStandardMaterial）

| 语义 | openPBR | aiSS | VRayMtl | RedshiftStandardMaterial |
|---|---|---|---|---|
| 漫反射权重/颜色/粗糙度 | `baseWeight`/`baseColor`/`baseDiffuseRoughness` | `base`/`baseColor`/`diffuseRoughness` | `diffuseColorAmount`/`color`/`roughnessAmount` | `base_color_weight`/`base_color`/`diffuse_roughness` |
| 金属度 | `baseMetalness` | `metalness` | `metalness` | `metalness` |
| 高光权重/颜色/粗糙度/IOR | `specularWeight`/`specularColor`/`specularRoughness`/`specularIOR` | `specular`/`specularColor`/`specularRoughness`/`specularIOR` | `reflectionColorAmount`/`reflectionColor`/`reflectionGlossiness`/`fresnelIOR` | `refl_weight`/`refl_color`/`refl_roughness`/`refl_ior` |
| 高光各向异性/旋转 | `specularRoughnessAnisotropy` | `specularAnisotropy`/`specularRotation` | `anisotropy`/`anisotropyRotation` | `refl_aniso`/`refl_aniso_rotation`（无旋转源，重置） |
| 透射权重/颜色/深度/散射 | `transmissionWeight`/`transmissionColor`/`transmissionDepth`/`transmissionScatter` | `transmission`/`transmissionColor`/`transmissionDepth`/`transmissionScatter` | `refractionColorAmount`/`refractionColor`/`fogDepth`/`fogColor` | `refr_weight`/`refr_color`/`ss_depth`/`ss_scatter_color` |
| 色散 | `transmissionDispersionAbbeNumber` | `transmissionDispersionAbbe`+`transmissionDispersion`(bool) | `refrDispersionAbbe`+`refrDispersionOn` | `refr_abbe` |
| 次表面权重/颜色 | `subsurfaceWeight`/`subsurfaceColor` | `subsurface`/`subsurfaceColor` | `translucencyAmount`/`translucencyColor` | `ms_amount`/`ms_color` |
| 次表面半径/缩放/各向异性 | `subsurfaceRadius`/`subsurfaceRadiusScale`/`subsurfaceScatterAnisotropy` | `subsurfaceRadius`/`subsurfaceScale`/`subsurfaceAnisotropy` | —（跳过） | `ms_radius`/`ms_radius_scale`/`ms_phase` |
| 绒毛权重/颜色/粗糙度 | `fuzzWeight`/`fuzzColor`/`fuzzRoughness` | `sheen`/`sheenColor`/`sheenRoughness` | `sheenColorAmount`/`sheenColor`/`sheenGlossiness` | `sheen_weight`/`sheen_color`/`sheen_roughness` |
| 涂层权重/颜色/粗糙度/IOR | `coatWeight`/`coatColor`/`coatRoughness`/`coatIOR` | `coat`/`coatColor`/`coatRoughness`/`coatIOR` | `coatColorAmount`/`coatColor`/`coatGlossiness`/`coatIOR` | `coat_weight`/`coat_color`/`coat_roughness`/`coat_ior` |
| 涂层各向异性/暗化 | `coatRoughnessAnisotropy`/`coatDarkening` | `coatAnisotropy`/`coatRotation`/— | `coatAnisotropy`/`coatAnisotropyRotation`/`coatDarkening` | `coat_aniso`/—（暗化无对应） |
| 薄膜 | `thinFilmWeight`/`thinFilmThickness`/`thinFilmIOR` | —/`thinFilmThickness`/`thinFilmIOR` | `thinFilmOn`/`thinFilmThickness`/`thinFilmIOR` | —/`thinfilm_thickness`/`thinfilm_ior` |
| 自发光 | `emissionLuminance`+`emissionColor` | `emission`+`emissionColor` | `illumColorAmount`(`emission_to_emission_luminance`)+`illumColor` | `emission_weight`(`clamp`)+`emission_color` |
| 透明度 | `geometryOpacity` | `opacity` | `opacityMap`（标量自动广播 RGB） | `opacity_color`（标量自动广播 RGB） |
| 薄壁/涂层法线 | `geometryThinWalled`/`geometryCoatNormal` | `thinWalled`/`coatNormal` | `refrThinWalled`/`coatBumpMap` | `refr_thin_walled`/`coat_bump_input` |
| GI 权重 | `aiIndirectDiffuse`/`aiIndirectSpecular` | `indirectDiffuse`/`indirectSpecular` | —（跳过） | `diffuse_indirect`/`refl_indirect` |
| GI 反射开关 | `aiInternalReflections` | `internalReflections` | `traceReflections` | —（跳过） |

> Redshift 附加说明：`thinFilmWeight`/`coatDarkening`/`aiMatteColor`/`aiCaustics`/`aiExitToBackground`/`aiInternalReflections` 无对应（跳过）；`ss_*` 为透射单散射、`ms_*` 为次表面多重散射，注意 `subsurfaceRadius`(颜色)→`ms_radius`、`subsurfaceRadiusScale`(标量)→`ms_radius_scale`。

## 一律跳过（映射为空）的属性

> 本清单**仅针对 VRayMtl 目标**。新增其他渲染器（如 Redshift）时，需按目标渲染器的属性集**重新评估**——在对应 `.mmap` 中这些属性可能都有合适目标，不受本清单约束。

- 遮罩类：`aiEnableMatte`、`aiMatteColor`（VRay 不互通）。
- 无对应：`aiCaustics`、`aiExitToBackground`、`indirectDiffuse`、`indirectSpecular`、`dielectricPriority`、`transmissionScatterAnisotropy`、`subsurfaceRadius*`、`subsurfaceScatterAnisotropy`、`subsurfaceScale`、`subsurfaceAnisotropy`、`subsurfaceType`、`coatAffectColor`、`coatAffectRoughness`、AOV/ID（`aiAovId*`/`aiId*`/`aovId*`/`id*`）。

## 已知反例（禁止模仿）

- `aiMatteColor`→`illumColor`（遮罩≠自发光）；`indirectDiffuse`→`diffuseColorG`；`subsurfaceScale`→`bumpDeltaScale`；`subsurfaceAnisotropy`→`anisotropyDerivation`；`coatAffectRoughness`→`reflectionAffectAlpha`；`transmissionDispersion`(bool)→`refrDispersionAbbe`(float)；`subsurfaceType`→`translucencyMode`（枚举不对应）；`baseColor`→`diffuseColorR`（颜色子通道）。
