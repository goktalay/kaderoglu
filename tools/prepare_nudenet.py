"""NudeNet modelini OpenCV DNN'in çalıştırabileceği hale getirir (bir kez çalıştırılır).

Kullanım: .venv\\Scripts\\python.exe tools\\prepare_nudenet.py models\\nudenet_320n.onnx models\\nudenet_320n_cv.onnx

Python 3.15 için onnxruntime olmadığından model OpenCV ile çalıştırılır. OpenCV 4 dinamik şekilleri
desteklemediği için giriş 1x3x320x320'ye sabitlenir ve şekle bağlı alt hesaplamalar (Shape, Gather,
ConstantOfShape...) önceden hesaplanıp sabit olarak modele gömülür.
"""
import sys

import cv2
import numpy as np
import onnx
from onnx import helper, numpy_helper, shape_inference
from onnx.reference import ReferenceEvaluator

SIZE = 320


def fold(model):
    g = model.graph
    known = {t.name: numpy_helper.to_array(t) for t in g.initializer}
    shapes = {}
    for vi in list(g.input) + list(g.value_info) + list(g.output):
        dims = [d.dim_value for d in vi.type.tensor_type.shape.dim]
        if dims and all(d > 0 for d in dims):
            shapes[vi.name] = dims
    keep = []
    for node in g.node:
        ins = [i for i in node.input if i]
        if node.op_type == "Constant":
            known[node.output[0]] = numpy_helper.to_array(node.attribute[0].t)
            continue
        if node.op_type == "Shape" and ins[0] in shapes:
            known[node.output[0]] = np.array(shapes[ins[0]], dtype=np.int64)
            continue
        if ins and all(i in known for i in ins):
            sub = helper.make_model(
                helper.make_graph([node], "f", [helper.make_tensor_value_info(i, helper.np_dtype_to_tensor_dtype(known[i].dtype), known[i].shape) for i in ins],
                                  [helper.make_tensor_value_info(o, onnx.TensorProto.UNDEFINED, None) for o in node.output]),
                opset_imports=model.opset_import)
            outs = ReferenceEvaluator(sub).run(None, {i: known[i] for i in ins})
            for o, v in zip(node.output, outs):
                known[o] = np.asarray(v)
            continue
        keep.append(node)
    used = {i for n in keep for i in n.input} | {o.name for o in g.output}
    del g.node[:]
    g.node.extend(keep)
    del g.initializer[:]
    g.initializer.extend(numpy_helper.from_array(v, k) for k, v in known.items() if k in used)
    return model


def main():
    src, dst = sys.argv[1], sys.argv[2]
    m = onnx.load(src)
    for d, v in zip(m.graph.input[0].type.tensor_type.shape.dim, (1, 3, SIZE, SIZE)):
        d.ClearField("dim_param")
        d.dim_value = v
    for o in m.graph.output:
        for d in o.type.tensor_type.shape.dim:
            d.ClearField("dim_param")
    del m.graph.value_info[:]
    m = shape_inference.infer_shapes(m)
    before = len(m.graph.node)
    m = fold(m)
    onnx.save(m, dst)
    print(f"{before} düğüm -> {len(m.graph.node)} düğüm")
    net = cv2.dnn.readNetFromONNX(dst)
    net.setInput(np.zeros((1, 3, SIZE, SIZE), np.float32))
    print("OpenCV çıktısı:", net.forward().shape)


if __name__ == "__main__":
    main()
